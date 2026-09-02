# Self-service Web UI — Implementation Plan

**Goal:** `docker compose up -d` is the only command. Every secret, key, marketplace
and search is created in the web UI. Nothing about *what to monitor* lives in
`.env` or `docker-compose.yml` any more.

**Reference spec:** `docs/superpowers/specs/2026-09-02-ui-selfservice-design.md`

**Branch:** `ui-improvements` (created off `main`, currently empty).

---

## The two blockers

Neither is cosmetic; both were hit for real while setting this instance up.

**1. The UI cannot start without credentials.** The image binds `0.0.0.0`, and
`webui/server.py` refuses a non-loopback bind unless it finds a username and
password — taken from a `[marketplace.*]` section or `FACEBOOK_USERNAME` /
`FACEBOOK_PASSWORD`. A fresh container therefore has **no UI in which to enter
credentials**. The monitor still runs, port 8467 is simply dead, which reads as
a broken container. Any "set it up in the UI" story dies here first.

**2. UI login is the marketplace login.** `webui/config_auth.py` reuses Facebook
credentials as the UI password. That conflates two identities, means a
tutti-only user has no way in, and is not something to ship to paying users.

---

## Architecture

**Own identity.** A `[webui]` section in `config.toml` holds `username` and a
bcrypt `password_hash` (`webui/auth.py` already has `hash_password` /
`verify_password`). Marketplace credentials become just another secret the UI
manages, never the door key.

**First run.** With no `[webui]` section the server starts in **setup mode**: it
binds as configured but every route except the setup flow is refused, and it
prints a one-time token plus URL to stdout — visible with `docker compose logs aimm`.
This is the Portainer / Paperless / Jellyfin pattern and the one CLI step that
genuinely cannot be removed: something must prove physical access to the host.
The token is generated per boot, held in memory only, and dies once an admin
account exists.

**Secrets move into `config.toml`.** The UI already masks and round-trips secrets
there (`webui/secrets_redact.py`), and the file is hot-reloaded on change — so a
token entered in the UI takes effect without a restart. Environment variables are
*not* reloadable: any `.env`-based flow would need `docker compose up -d` on every
change, which puts the user back on the CLI. `${ENV_VAR}` stays supported for
people who prefer docker secrets, but the UI writes real values.

**Structured config API.** Today the only write path is a whole-file
`PUT /api/config/file/{id}`. Forms need per-section reads, writes and field-level
errors. The field list is *derived from the dataclasses* (`TuttiItemConfig`,
`FacebookItemConfig`, …) rather than hardcoded in JS, so adding a marketplace
option surfaces in the UI automatically. Format-preserving TOML editing already
exists client-side as the vendored `toml-edit-js` WASM — reuse it and add no
Python dependency.

**What deliberately stays in compose:** port mapping, the volume, `restart:`,
`TZ`, and the `AIMM_*` display/VNC variables. That is infrastructure, correctly
the orchestrator's job — a UI cannot rebind its own port. `.env` keeps only an
optional `AIMM_ADMIN_PASSWORD` for unattended provisioning.

---

## Global constraints

Same as the existing plans in this directory, plus:

- **Lint:** ruff + black, `line-length = 99`, `D205` enforced, google docstrings.
- **Types:** every function annotated, including tests; `mypy` over `src`.
- **Broad excepts** re-raise `KeyboardInterrupt` first.
- **Auth:** reads use `Depends(require_session)`; all writes additionally
  `require_csrf`, matching the existing `PUT /api/config/file/{id}`.
- **Do not commit `uv.lock`** — it is stale against `pyproject.toml` and any
  `uv sync` rewrites it.
- **Test env on this host:** `HOME=<scratch>` and
  `PLAYWRIGHT_BROWSERS_PATH=/home/dockeradmin/.cache/ms-playwright`, plus
  `LD_LIBRARY_PATH` to the extracted sysroot. See the memory file
  `aimm-verification-workflow`.
- **Fonts are vendored**, never fetched from a CDN at runtime.

---

## Phases

Each phase is independently shippable and leaves the app working.

### Phase 1 — Identity and first run *(done)*

- [x] Account in its own `webui.toml`, **not** a `[webui]` section in
      `config.toml` as originally planned. Three reasons found while building:
      `Config.validate_sections` rejects unknown top-level sections, the UI
      rewrites `config.toml` on every edit, and a bcrypt hash has no business
      in the file a user opens to change a search.
- [x] `webui/setup.py`: account file (0600, atomic create), validation,
      one-time boot token, unattended provisioning from `AIMM_ADMIN_PASSWORD`.
- [x] `GET /api/setup/status`, `POST /api/setup/account` — one round trip
      rather than the planned claim/admin pair; there was no state worth
      keeping between them.
- [x] Startup banner prints the token when no account exists.
- [x] `_resolve_auth` prefers the account file, falls back to marketplace
      credentials, and only then enters setup mode. Loopback keeps its
      password-free behaviour.
- [x] **Fixed a pre-existing bug this uncovered:** the config the container
      writes on first run contains `username = "${FACEBOOK_USERNAME}"`, and
      `extract_credentials` took the unresolved placeholder literally. A fresh
      container therefore believed it had credentials, showed
      `user: ${FACEBOOK_USERNAME}` in the banner, and could never be logged
      into. Placeholders now resolve against the environment and count as
      absent when unset.
- [x] Tests (33 new) plus a live container walk-through: empty config dir →
      setup page, app and login refused with 403, wrong token 401, weak
      password 400, account created, app opens, token reuse 409, account
      survives restart, normal login works.

### Phase 2 — Structured config API *(done)*

- [x] `webui/schema.py` — field descriptors from `dataclasses.fields()`, so a
      new marketplace option reaches the form the day it is added. Choices,
      help and secret-ness cannot be read off a validator, so they live in
      `FIELD_HINTS`; a field with no hint still appears as a text input.
- [x] One derivation cannot be automatic: `search_phrases` carries
      `default_factory=list` yet its validator rejects an empty list. `FieldHint`
      gained a `required` override for that.
- [x] `webui/sections.py` — read, render, validate and splice one section.
- [x] `GET /api/schema`, `GET /api/sections`, `GET|PUT|DELETE
      /api/sections/{kind}[/{name}]`, `POST /api/sections/{kind}` and
      `POST /api/sections/{kind}/validate` for live field errors.
- [x] Secrets masked on read and round-tripped on write, so a form that never
      saw a token cannot blank it.
- [x] **Two bugs the live container found that unit tests had not.** A saved
      item carried no `marketplace = …`, so the loader bound it to whichever
      marketplace section came first and tutti options landed on a facebook
      config; the discriminator is now written for item, marketplace and ai,
      and an item's variant resolves through its marketplace section's
      `market_type`, so a section named anything still works. And deleting left
      a blank line behind: the splice now normalises the seam to one blank line
      rather than pretending a delete can be the byte-exact inverse of an
      insert.
- [x] Tests (44 new) plus a live run against the real config: invalid canton
      returns a field error and touches nothing, a valid item is written and
      picked up, comments and neighbouring sections survive, and three
      add/delete cycles leave the file byte-identical.

### Phase 3 — Diagnostics *(done)*

- [x] `webui/diagnostics.py` with `check_notification`, `check_ai`,
      `clear_cache`, `health`. Named `check_*` rather than `test_*`: pytest
      collects any imported `test_*` callable as a test case, which broke the
      suite until they were renamed.
- [x] `POST /api/test/notification` sends a **real** push. Nothing is mocked —
      a token the provider rejects is exactly what this is for, and only a real
      send catches it. `force=True`, since the sample counts as already
      notified from the previous run and a test that works once is not a test.
- [x] `POST /api/test/ai` connects **and** rates a sample, because a model that
      answers but cannot follow the rating format is just as broken. It builds
      a **neutral probe** rather than borrowing one of the user's hunts: a hunt
      narrowed to "Hero 13" rated the HERO11 sample 1/5, which reads as a
      broken backend when the backend is fine. Found by running it live. The
      probe also works before any hunt exists.
- [x] `GET /api/health` — marketplaces with enabled/needs-login/credentials,
      items with their counters, the AI backend, and each user's configured
      notification methods. It deliberately does **not** report a "next run"
      time: the scheduler lives in the monitor process, the web UI cannot see
      it, and an invented number is worse than none.
- [x] `POST /api/cache/clear` with a scope, so clearing listings does not throw
      away the AI answers.
- [x] Tests (24 new) with the outermost call patched, so CI stays offline while
      a regression in the wiring still fails. Verified live: health reports the
      real counters, the AI test returns 4/5 in ~1 s against the Ollama box,
      and a real push arrived on the phone.

### Phase 3.5 — Marketplace selection *(done)*

- [x] **Global switches** — `enabled` on a `[marketplace.*]` section. Already
      honoured by `validate_items` and `schedule_jobs`; now covered by tests
      that pin the behaviour, so the UI switch in Phase 5 is wiring only. A
      disabled marketplace stops being searched and keeps its hunts.
- [x] **Per-hunt selection** — `marketplace` accepts a list. One authored item
      becomes one runtime config per marketplace, each validated by that
      marketplace's own class.
- [x] `.name` stays the authored name so counters, notifications and log lines
      keep talking about one hunt; only the dict key is suffixed
      (`gopro@tutti`), and only when there is more than one target. An item on
      a single marketplace keeps its plain key, so every existing config and
      test is untouched.
- [x] `Config.find_item()` / `item_names()` resolve a hunt by its authored
      name, because that is what a person types for `--check`.
- [x] An absent `marketplace` still means *the first* marketplace, not all of
      them — the alternative would silently double the searches of every
      existing config.
- [x] A hunt on several marketplaces may only use options all of them accept.
      `canton` on a tutti+facebook hunt is now a field error naming the
      marketplace that refused it, rather than a crash mid-search.
- [x] Tests (16 new) and a live run: an invalid combination is rejected with
      "Diese Option kennt facebook nicht", a valid one is written as
      `marketplace = ["tutti", "facebook"]`, and the monitor schedules it
      twice — every 30 minutes on facebook, hourly on tutti.

### Phase 3.6 — One price reference across all marketplaces *(done)*

- [x] `price_index.py` on the existing diskcache, with
      `CacheType.PRICE_OBSERVATION`. Records are kept **per hunt** rather than
      one key per listing: a hunt is what gets read back, the monitor is single
      threaded so read-modify-write is safe, and scanning every key to find one
      hunt's prices would not be.
- [x] Both marketplaces now *record* observations and read the going rate back
      out of the index. `price_reference()` and the inline block are gone;
      `reference_prices()` became `observations()`, returning typed records
      that carry their marketplace and currency.
- [x] Re-seeing a listing overwrites its record instead of counting twice — the
      same offer in ten consecutive runs must not outweigh its neighbours.
- [x] 14-day freshness window, pruned on write and filtered on read.
- [x] Currency: converted to the judged listing's currency, and anything
      unconvertible is **dropped** rather than mixed in.
- [x] The readout names its basis — "35 comparable listings (5 facebook, 30
      tutti)" — and stays silent when only one marketplace contributed.
- [x] Facebook's second, unfiltered search is kept and now feeds the index, so
      its observations are not biased towards the configured bounds.
- [x] **A silent edit failure this caught.** The call site in `tutti.py` never
      received the `composition` argument — one of the string replacements did
      not match after formatting and had no assertion on it. Unit tests passed;
      only the live run showed the missing "(5 facebook, 30 tutti)". Both call
      sites now have a test pinning them.
- [x] Tests (26 new) and a live proof: five facebook observations recorded
      twenty minutes earlier were pooled with thirty fresh tutti ones into one
      median of CHF 135 from 35 listings.

### Phase 4 — The finds feed

- [ ] `GET /api/found` — paginated, filterable by hunt, joining the same three
      cache namespaces `found_export.py` already joins. Refactor that module so
      CSV export and this endpoint share one join.
- [ ] Include `price_comparison` and the parsed `PriceStats` so the client can draw
      the ruler without re-deriving it.

### Phase 5 — The interface

Per the design spec. Vanilla JS, matching the existing stack — no framework.

- [ ] Tokens, vendored fonts, base layout (rail + feed + log drawer).
- [ ] Find card with the **price ruler**.
- [ ] Hunt form generated from `/api/schema`, with the marketplace checkboxes
      driving which per-marketplace fields appear.
- [ ] Connections screen with inline Test buttons.
- [ ] Setup wizard: token → admin → marketplace → first hunt.
- [ ] Expert TOML editor retained behind Settings.
- [ ] States: loading, empty, error on every list. Responsive to 390px.
- [ ] Verify in a real browser at desktop and mobile widths before presenting.

### Phase 6 — Docs

- [ ] README quick start becomes: `docker compose up -d`, read the token, done.
- [ ] `.env.example` shrinks to the optional admin password.
- [ ] CHANGELOG.

---

## Deliberately out of scope

Worth stating because of the subscription ambition: this plan makes the product
**self-service for one owner**. It does not make it **multi-tenant**. Accounts
per customer, data isolation, billing, and a hosted control plane are a separate
and much larger project — the monitor currently assumes one config file, one
cache and one browser. Phase 1 is the prerequisite for that work, not a
down-payment on it.

---

## Decisions

Settled with the client 2026-09-02; no open questions remain.

1. **UI language: German.** All strings go through one dictionary
   (`webui/static/strings.de.js`) so a second locale is a data change rather
   than a refactor. Code, comments and identifiers stay English.
2. **The fork stays private.** No PRs back to BoPeng. The UI may therefore
   diverge as far as it needs to; no upstream compatibility constraint.
3. **Facebook credentials live in `config.toml`.** Accepted. `${ENV_VAR}`
   indirection keeps working for anyone who prefers docker secrets, but the UI
   writes real values so a change takes effect on hot-reload. The file is
   `0600` root inside the container and the UI masks the value on read.
4. **Subscription is not a goal right now.** It was context for how the UI
   should *look*, not a feature request — so the "out of scope" section above
   stands, and nothing multi-tenant gets built. The bar is: it should look like
   something one could sell.
