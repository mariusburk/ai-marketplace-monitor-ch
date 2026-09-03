"""FastAPI app factory and uvicorn-in-a-thread runner.

The monitor process stays fully synchronous. Uvicorn runs on its own
asyncio loop in a daemon thread; the LogBroadcastHandler bridges records
from the main thread to that loop via ``loop.call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import mimetypes
import os
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import uvicorn
from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..utils import cache
from .auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    SESSION_TTL,
    AuthConfig,
    RateLimiter,
    SessionManager,
    hash_password,
    verify_password,
)
from .config_api import ConfigFileService
from .config_auth import extract_credentials
from .diagnostics import check_ai, check_notification, clear_cache, health, probe_ollama
from .found_export import iter_found_csv, iter_found_records, iter_found_rows
from .log_handler import LogBroadcastHandler
from .schema import config_schema
from .sections import SectionError, SectionService, validate_values
from .setup import (
    SetupError,
    WebUIAccount,
    create_account,
    default_account_file,
    generate_setup_token,
    provision_from_environment,
    read_account,
    token_matches,
)

# Ensure the vendored toml-edit-js WASM bundle is served with the right
# Content-Type. Python's mimetypes module learned .wasm in 3.10 but
# explicit registration is safer across patch versions.
mimetypes.add_type("application/wasm", ".wasm")

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class WebUIConfig:
    host: str = "127.0.0.1"
    port: int = 8467
    config_files: List[Path] = field(default_factory=list)
    log_handler: LogBroadcastHandler | None = None
    # Where the web UI's own account lives. Kept out of config.toml on purpose
    # — see webui/setup.py. Overridable so tests need no home directory.
    account_file: Path | None = None


@dataclass
class StartupInfo:
    """Information about the running server, shown in the startup banner."""

    urls: List[str]
    username: str | None  # None in open mode
    host: str
    port: int
    exposed: bool
    # Set only while the instance has no account: the one-time token that
    # unlocks the setup flow, printed to the log for the operator to copy.
    setup_token: str | None = None


class AuthState:
    """Mutable auth state.

    On loopback (default) the web UI is always open — no password required.
    When ``--webui-host`` exposes the server on a non-loopback interface,
    ``auth`` must be set: from the web UI's own account file, or failing that
    from a marketplace config section or environment variables.

    A third state exists for a fresh install: no account anywhere, in which
    case ``setup_token`` is set and only the setup routes answer. See
    ``webui/setup.py`` for why that beats refusing to start.
    """

    def __init__(self) -> None:
        self.auth: AuthConfig | None = None
        self.exposed: bool = False
        self.setup_token: str | None = None
        self.account_file: Path = default_account_file()

    @property
    def setup_required(self) -> bool:
        return self.setup_token is not None

    def adopt(self, account: WebUIAccount) -> None:
        """Switch to a real account, ending setup mode."""
        self.auth = AuthConfig(
            username=account.username,
            password_hash=account.password_hash,
            secret_key=account.session_secret,
        )
        self.setup_token = None


def _resolve_auth(config: WebUIConfig) -> tuple[AuthState, StartupInfo]:
    """Build initial AuthState from config files and environment.

    On loopback the UI is always open. When exposed (--webui-host), an account
    is required, resolved in order of precedence:

    1. the web UI's own account file, written by the setup flow;
    2. ``AIMM_ADMIN_PASSWORD``, which provisions that file unattended;
    3. a ``[marketplace.*]`` section or ``FACEBOOK_USERNAME`` /
       ``FACEBOOK_PASSWORD`` — kept so existing installs keep working, and
       deprecated in favour of (1);
    4. nothing at all, which starts setup mode rather than refusing to boot.
    """
    exposed = config.host not in ("127.0.0.1", "localhost", "::1")
    state = AuthState()
    state.exposed = exposed
    state.account_file = config.account_file or default_account_file()

    account = read_account(state.account_file)
    if account is None:
        try:
            account = provision_from_environment(state.account_file)
        except (SetupError, OSError):
            # A bad AIMM_ADMIN_PASSWORD must not wedge the boot — fall through
            # to setup mode, where the operator can fix it in the browser.
            account = None
    if account is not None:
        state.adopt(account)

    if exposed and state.auth is None:
        extracted = extract_credentials(config.config_files)
        if extracted.username and extracted.password:
            state.auth = AuthConfig(
                username=extracted.username,
                password_hash=hash_password(extracted.password),
                secret_key=secrets.token_urlsafe(32),
            )
        else:
            # Nothing to authenticate against. Rather than refuse to start and
            # leave the operator with a dead port, serve the setup flow.
            state.setup_token = generate_setup_token()

    info = StartupInfo(
        setup_token=state.setup_token,
        urls=_enumerate_urls(config.host, config.port),
        username=state.auth.username if state.auth else None,
        host=config.host,
        port=config.port,
        exposed=exposed,
    )
    return state, info


def _set_session_cookies(response: Response, token: str, csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="strict",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=SESSION_TTL,
        httponly=False,  # JS reads this to echo via header
        samesite="strict",
    )


def _enumerate_urls(host: str, port: int) -> List[str]:
    if host in ("127.0.0.1", "localhost", "::1"):
        return [f"http://127.0.0.1:{port}"]
    if host in ("0.0.0.0", "::"):  # noqa: S104 — intentional bind-all
        # Enumerate local interface addresses so the user sees every reachable URL.
        urls = [f"http://127.0.0.1:{port}"]
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None):
                addr = str(info[4][0])
                if addr and addr not in ("127.0.0.1", "::1"):
                    if ":" in addr:
                        urls.append(f"http://[{addr}]:{port}")
                    else:
                        urls.append(f"http://{addr}:{port}")
        except socket.gaierror:
            pass
        # De-duplicate preserving order.
        seen: set[str] = set()
        unique: List[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique
    return [f"http://{host}:{port}"]


def create_app(
    config: WebUIConfig,
    state: AuthState,
    config_service: ConfigFileService,
    log_handler: LogBroadcastHandler,
) -> FastAPI:
    app = FastAPI(
        title="AI Marketplace Monitor",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    section_service = SectionService(config_service)

    process_secret = secrets.token_urlsafe(32)
    sessions = SessionManager(process_secret)
    rate_limiter = RateLimiter()

    def is_open() -> bool:
        """True when running on loopback — no password required."""
        return not state.exposed and not state.setup_required

    def require_session(
        request: Request,
        session: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> str:
        # Until an account exists nothing but the setup flow answers, on
        # loopback as much as when exposed. Otherwise a fresh container on
        # 127.0.0.1 would hand out an open session and the setup step could
        # be skipped entirely.
        if state.setup_required:
            raise HTTPException(status_code=403, detail="Setup required")
        if is_open():
            return "anonymous"
        if session is None:
            raise HTTPException(status_code=401, detail="Not authenticated")
        username = sessions.validate(session)
        if username is None:
            raise HTTPException(status_code=401, detail="Session expired")
        return username

    def require_csrf(
        request: Request,
        csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    ) -> None:
        if is_open():
            return  # open mode skips CSRF (nothing to protect)
        header = request.headers.get(CSRF_HEADER)
        if not header or not csrf_cookie or not secrets.compare_digest(header, csrf_cookie):
            raise HTTPException(status_code=403, detail="CSRF token mismatch")

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/api/setup/status")
    async def setup_status() -> Dict[str, Any]:
        """Tell the client which screen to show. Deliberately unauthenticated.

        It leaks only whether an account exists, which an attacker learns from
        the login screen anyway.
        """
        return {"setup_required": state.setup_required}

    @app.post("/api/setup/account")
    async def setup_account(
        request: Request,
        response: Response,
        token: str = Form(""),
        username: str = Form(""),
        password: str = Form(""),
    ) -> Dict[str, Any]:
        """Claim a fresh instance: exchange the boot token for an account."""
        if not state.setup_required:
            raise HTTPException(status_code=409, detail="Setup already completed")

        client_ip = request.client.host if request.client else "unknown"
        if rate_limiter.is_locked(client_ip):
            raise HTTPException(status_code=429, detail="Too many failed attempts")
        if not token_matches(state.setup_token, token):
            rate_limiter.record_failure(client_ip)
            raise HTTPException(status_code=401, detail="Invalid setup token")

        try:
            account = create_account(state.account_file, username, password)
        except SetupError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"Konto konnte nicht gespeichert werden: {exc}"
            ) from exc

        rate_limiter.reset(client_ip)
        state.adopt(account)
        # The signer is bound to the process secret, so the session issued here
        # stays valid for this run; the stored secret takes over on restart.
        session_token, csrf = sessions.issue(account.username)
        _set_session_cookies(response, session_token, csrf)
        return {"username": account.username, "csrf": csrf}

    @app.get("/api/auth/info")
    async def auth_info() -> Dict[str, Any]:
        """Return auth mode info for the frontend login screen."""
        return {
            "open": is_open(),
            "setup_required": state.setup_required,
            "username_hint": state.auth.username if state.auth else None,
        }

    @app.post("/api/login")
    async def login(
        request: Request,
        response: Response,
        username: str = Form(""),
        password: str = Form(""),
    ) -> Dict[str, Any]:
        if state.setup_required:
            raise HTTPException(status_code=403, detail="Setup required")

        # Loopback — always open, no password needed.
        if is_open():
            token, csrf = sessions.issue("anonymous")
            _set_session_cookies(response, token, csrf)
            return {"username": "anonymous", "csrf": csrf}

        # Exposed — credentials required.
        client_ip = request.client.host if request.client else "unknown"
        if rate_limiter.is_locked(client_ip):
            raise HTTPException(status_code=429, detail="Too many failed attempts")

        assert state.auth is not None  # enforced by start_webui()
        if username != state.auth.username or not verify_password(
            password, state.auth.password_hash
        ):
            rate_limiter.record_failure(client_ip)
            raise HTTPException(status_code=401, detail="Invalid credentials")

        rate_limiter.reset(client_ip)
        token, csrf = sessions.issue(username)
        _set_session_cookies(response, token, csrf)
        return {"username": username, "csrf": csrf}

    @app.post("/api/logout")
    async def logout(response: Response) -> Dict[str, Any]:
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)
        return {"ok": True}

    @app.get("/api/status")
    async def status(_: str = Depends(require_session)) -> Dict[str, Any]:
        files = config_service.list_files()
        return {
            "config_files": [f.__dict__ for f in files],
            "urls": _enumerate_urls(config.host, config.port),
            "auth_mode": "open" if is_open() else "authenticated",
            "open": is_open(),
            "vnc_enabled": os.environ.get("AIMM_ENABLE_VNC") == "1"
            and Path(os.environ.get("AIMM_NOVNC_DIR", "/usr/share/novnc")).is_dir(),
        }

    @app.get("/api/config/files")
    async def list_config_files(_: str = Depends(require_session)) -> Dict[str, Any]:
        return {"files": [f.__dict__ for f in config_service.list_files()]}

    @app.get("/api/config/file/{file_id}")
    async def get_config_file(file_id: str, _: str = Depends(require_session)) -> Dict[str, Any]:
        try:
            content, mtime = config_service.read(file_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        from .config_api import scan_sections
        from .secrets_redact import MASK, has_mask

        sections = [
            {
                "name": s.name,
                "prefix": s.prefix,
                "suffix": s.suffix,
                "line_start": s.line_start,
                "line_end": s.line_end,
                "fields": s.fields,
            }
            for s in scan_sections(content)
        ]
        return {
            "content": content,
            "mtime": mtime,
            "has_masked_secrets": has_mask(content),
            "mask_token": MASK,
            "sections": sections,
        }

    @app.put("/api/config/file/{file_id}", response_model=None)
    async def put_config_file(
        file_id: str,
        body: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="Missing 'content' field")
        base_mtime = body.get("base_mtime")
        try:
            new_mtime, ok, error = config_service.write(
                file_id, content, base_mtime if isinstance(base_mtime, (int, float)) else None
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from None
        if not ok:
            status_code = 409 if error and "conflict" in error else 400
            return JSONResponse(  # type: ignore[return-value]
                status_code=status_code,
                content={"ok": False, "error": error, "mtime": new_mtime},
            )
        return {"ok": True, "mtime": new_mtime}

    @app.post("/api/config/validate")
    async def validate_config(
        body: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        content = body.get("content")
        if not isinstance(content, str):
            raise HTTPException(status_code=400, detail="Missing 'content' field")
        ok, error = config_service.validate(content)
        return {"valid": ok, "error": error}

    @app.post("/api/monitor/restart")
    async def restart_monitor(
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        """Wake the monitor by touching the config file.

        The file watcher interrupts the monitor's doze() sleep, causing
        it to reload the config and run all scheduled searches immediately.
        """
        try:
            path = config_service.editable_path
            path.touch()
            return {"ok": True, "message": "Monitor woken — searching all items now."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to touch config: {e}") from e

    # ------------------------------------------------------------------
    # Structured config: what the forms are built from and written through
    # ------------------------------------------------------------------

    @app.get("/api/schema")
    async def schema(_: str = Depends(require_session)) -> Dict[str, Any]:
        """Field descriptions, derived from the config dataclasses."""
        return config_schema()

    @app.get("/api/sections")
    async def list_sections(_: str = Depends(require_session)) -> Dict[str, Any]:
        return {"sections": section_service.list_sections()}

    @app.get("/api/sections/{kind}/{name}")
    async def get_section(
        kind: str, name: str, _: str = Depends(require_session)
    ) -> Dict[str, Any]:
        try:
            return section_service.get_section(kind, name)
        except SectionError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/sections/{kind}")
    async def create_section(
        kind: str,
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        return _save(kind, payload, create=True)

    @app.put("/api/sections/{kind}/{name}")
    async def update_section(
        kind: str,
        name: str,
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        return _save(kind, {**payload, "name": name}, create=False)

    @app.delete("/api/sections/{kind}/{name}")
    async def delete_section(
        kind: str,
        name: str,
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        try:
            section_service.delete_section(kind, name)
        except SectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True}

    @app.post("/api/sections/{kind}/validate")
    async def validate_section(
        kind: str, payload: Dict[str, Any], _: str = Depends(require_session)
    ) -> Dict[str, Any]:
        """Check a section without writing it, so a form can mark fields live."""
        try:
            errors = validate_values(
                kind,
                payload.get("variant"),
                str(payload.get("name") or "probe"),
                payload.get("values") or {},
            )
        except SectionError as exc:
            return {"ok": False, "errors": {"": str(exc)}}
        return {"ok": not errors, "errors": errors}

    def _save(kind: str, payload: Dict[str, Any], *, create: bool) -> Dict[str, Any]:
        try:
            errors = section_service.save_section(
                kind,
                str(payload.get("name") or ""),
                payload.get("variant"),
                payload.get("values") or {},
                create=create,
            )
        except SectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if errors:
            return {"ok": False, "errors": errors}
        return {"ok": True, "errors": {}}

    # ------------------------------------------------------------------
    # Diagnostics: the checks that used to need a shell
    # ------------------------------------------------------------------

    @app.get("/api/found")
    async def found(
        item: str = "",
        marketplace: str = "",
        limit: int = 50,
        offset: int = 0,
        _: str = Depends(require_session),
    ) -> Dict[str, Any]:
        """The finds feed: what was actually notified, newest first.

        Shares its join with the CSV export — both read the same three cache
        namespaces, and two joins would have drifted apart.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        records = [
            record
            for record in iter_found_records(cache)
            if (not item or record["item"] == item)
            and (not marketplace or record["marketplace"] == marketplace)
        ]
        page = records[offset : offset + limit]
        return {
            "finds": page,
            "total": len(records),
            "offset": offset,
            "limit": limit,
            "items": sorted({r["item"] for r in records if r["item"]}),
            "marketplaces": sorted({r["marketplace"] for r in records if r["marketplace"]}),
        }

    @app.get("/api/health")
    async def health_check(_: str = Depends(require_session)) -> Dict[str, Any]:
        return health(config.config_files)

    @app.post("/api/test/notification")
    async def notification_check(
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        """Send one real notification.

        Nothing is mocked: a token the provider rejects is exactly what
        this is meant to catch, and only a real send catches it.
        """
        result = check_notification(config.config_files, str(payload.get("user") or ""))
        return dataclasses.asdict(result)

    @app.post("/api/test/ai")
    async def ai_check(
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        result = check_ai(config.config_files, str(payload.get("ai") or ""))
        return dataclasses.asdict(result)

    @app.post("/api/test/ollama")
    async def ollama_probe(
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        """Ask an Ollama server what it has, from the address in the form.

        Unlike the other checks this one runs against unsaved input: it is what
        turns "type the model name exactly right" into picking from a list.
        """
        result = probe_ollama(str(payload.get("base_url") or ""))
        return dataclasses.asdict(result)

    @app.post("/api/cache/clear")
    async def clear(
        payload: Dict[str, Any],
        _: str = Depends(require_session),
        __: None = Depends(require_csrf),
    ) -> Dict[str, Any]:
        result = clear_cache(str(payload.get("scope") or "all"))
        return dataclasses.asdict(result)

    @app.get("/api/logs")
    async def get_logs(
        limit: int = 500,
        level: str = "DEBUG",
        kind: str | None = None,
        item: str | None = None,
        min_score: int | None = None,
        _: str = Depends(require_session),
    ) -> Dict[str, Any]:
        level_value = logging.getLevelName(level.upper())
        if not isinstance(level_value, int):
            level_value = 0
        return {
            "records": log_handler.snapshot(
                limit=limit,
                min_level=level_value,
                kind=kind,
                item=item,
                min_score=min_score,
            ),
            "capacity": log_handler._buffer.maxlen,
        }

    @app.websocket("/ws/stream")
    async def ws_stream(websocket: WebSocket) -> None:
        # In open mode (loopback) skip cookie check; otherwise require
        # a valid session cookie on the WebSocket handshake.
        if not is_open():
            session = websocket.cookies.get(SESSION_COOKIE)
            if not session or sessions.validate(session) is None:
                await websocket.close(code=4401)
                return

        await websocket.accept()
        queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=1000)
        log_handler.subscribe(queue)
        try:
            # Send a brief hello so clients know the stream is live.
            await websocket.send_json({"type": "hello", "time": time.time()})
            while True:
                payload = await queue.get()
                await websocket.send_json({"type": "log", "record": payload})
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: S110 — client disconnected; nothing to handle
            pass
        finally:
            log_handler.unsubscribe(queue)

    # ------------------------------------------------------------------
    # Optional noVNC bridge (Docker deployments)
    # ------------------------------------------------------------------
    novnc_dir = os.environ.get("AIMM_NOVNC_DIR", "/usr/share/novnc")
    vnc_host = os.environ.get("AIMM_VNC_HOST", "127.0.0.1")
    vnc_port = int(os.environ.get("AIMM_VNC_PORT", "5900"))
    if os.environ.get("AIMM_ENABLE_VNC") == "1" and Path(novnc_dir).is_dir():
        app.mount("/vnc", StaticFiles(directory=novnc_dir, html=True), name="vnc")

        @app.websocket("/ws/vnc")
        async def ws_vnc(websocket: WebSocket) -> None:
            if not is_open():
                session = websocket.cookies.get(SESSION_COOKIE)
                if not session or sessions.validate(session) is None:
                    await websocket.close(code=4401)
                    return
            await websocket.accept(subprotocol="binary")
            try:
                reader, writer = await asyncio.open_connection(vnc_host, vnc_port)
            except OSError:
                await websocket.close(code=1011)
                return

            async def ws_to_tcp() -> None:
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        writer.write(data)
                        await writer.drain()
                except WebSocketDisconnect:
                    pass
                finally:
                    writer.close()

            async def tcp_to_ws() -> None:
                try:
                    while True:
                        chunk = await reader.read(65536)
                        if not chunk:
                            break
                        await websocket.send_bytes(chunk)
                finally:
                    try:
                        await websocket.close()
                    except Exception:  # noqa: S110 — already closed
                        pass

            await asyncio.gather(ws_to_tcp(), tcp_to_ws(), return_exceptions=True)

    # ------------------------------------------------------------------
    # Static UI
    # ------------------------------------------------------------------
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    # Sync def (not async): FastAPI runs it in a threadpool and Starlette
    # iterates the sync generator there too, so the blocking cache scan never
    # runs on the event loop. The body streams row-by-row rather than buffering
    # the whole CSV, keeping memory bounded for large exports.
    @app.get("/api/found.csv")
    def export_found_csv(_: str = Depends(require_session)) -> StreamingResponse:
        filename = f"found-items-{time.strftime('%Y%m%d-%H%M%S')}.csv"
        return StreamingResponse(
            iter_found_csv(iter_found_rows(cache)),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


# ----------------------------------------------------------------------
# Thread runner
# ----------------------------------------------------------------------


class WebUIServer:
    """Runs uvicorn in a background thread."""

    def __init__(
        self,
        config: WebUIConfig,
        state: AuthState,
        config_service: ConfigFileService,
    ) -> None:
        if config.log_handler is None:
            raise ValueError("WebUIConfig.log_handler is required")
        self._config = config
        self._state = state
        self._config_service = config_service
        self._app = create_app(config, state, config_service, config.log_handler)
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        uv_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_level="warning",
            access_log=False,
            lifespan="off",
        )
        self._server = uvicorn.Server(uv_config)

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            assert self._config.log_handler is not None
            self._config.log_handler.attach_loop(loop)
            self._ready.set()
            try:
                loop.run_until_complete(self._server.serve())  # type: ignore[union-attr]
            finally:
                loop.close()

        self._thread = threading.Thread(target=runner, name="aimm-webui", daemon=True)
        self._thread.start()
        # Give the loop a moment to bind so attach_loop completes before
        # any log records are emitted.
        self._ready.wait(timeout=5)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True


def start_webui(
    config: WebUIConfig, logger: logging.Logger | None = None
) -> tuple[WebUIServer, StartupInfo]:
    """Resolve auth, build the service, and start the server thread."""
    if config.log_handler is None:
        raise ValueError("WebUIConfig.log_handler is required")
    state, info = _resolve_auth(config)

    # An exposed UI with no account no longer refuses to start: it serves the
    # first-run setup flow instead, gated by the one-time token in the log.
    if state.exposed and state.auth is None and not state.setup_required:
        raise RuntimeError(
            f"--webui-host {config.host} requires authentication and no setup "
            "token could be issued."
        )

    config_service = ConfigFileService(config.config_files, logger=logger)
    server = WebUIServer(config, state, config_service)
    server.start()
    return server, info
