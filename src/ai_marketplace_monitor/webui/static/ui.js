/* ---------------------------------------------------------------------------
   Fundstück — the views that replaced the TOML editor as the way in.

   app.js still owns the config editor, the log stream and the login POST; this
   file owns everything else: first-run setup, navigation, the finds feed, the
   hunt forms built from /api/schema, and the connection tests. The two share
   the DOM ids app.js already looked for, so neither had to be rewritten.

   Deliberately no framework: the existing front end is vanilla and one page of
   views does not earn a build step.
   --------------------------------------------------------------------------- */
(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const cookie = (name) =>
    document.cookie.split("; ").find((c) => c.startsWith(name + "="))?.split("=")[1] || "";

  const state = { schema: null, sections: [], hunt: "", health: null };

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", headers: {}, ...options };
    if (opts.body && typeof opts.body !== "string") {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.body);
    }
    if (opts.method && opts.method !== "GET") {
      opts.headers["X-CSRF-Token"] = cookie("aimm_csrf");
    }
    const res = await fetch(path, opts);
    let payload = null;
    try {
      payload = await res.json();
    } catch (_) {
      /* empty body is fine */
    }
    if (!res.ok) {
      const detail = (payload && payload.detail) || `HTTP ${res.status}`;
      throw new Error(detail);
    }
    return payload;
  }

  /* ---------- First run ------------------------------------------------- */

  async function setupNeeded() {
    try {
      const body = await api("/api/setup/status");
      return Boolean(body && body.setup_required);
    } catch (_) {
      return false;
    }
  }

  function wireSetup() {
    const form = $("#setup-form");
    if (!form) return;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const error = $("#setup-error");
      error.hidden = true;
      const data = new FormData();
      data.append("token", $("#setup-token").value.trim());
      data.append("username", $("#setup-user").value.trim());
      data.append("password", $("#setup-pass").value);
      try {
        const res = await fetch("/api/setup/account", {
          method: "POST",
          credentials: "same-origin",
          body: data,
        });
        const payload = await res.json().catch(() => null);
        if (!res.ok) throw new Error((payload && payload.detail) || `HTTP ${res.status}`);
        location.reload();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      }
    });
  }

  /* ---------- Navigation ------------------------------------------------ */

  function showView(name) {
    document.querySelectorAll(".view").forEach((view) => {
      view.classList.toggle("active", view.id === `view-${name}`);
    });
    document.querySelectorAll("#nav button").forEach((btn) => {
      btn.setAttribute("aria-current", String(btn.dataset.view === name));
    });
    if (name === "finds") loadFeed();
    if (name === "hunts") renderHunts();
    if (name === "connections") renderConnections();
    if (name === "settings") renderSettings();
  }

  /* ---------- The price ruler ------------------------------------------- */

  function ruler(find) {
    const basis = find.price_basis || {};
    const wrap = el("div");
    if (!basis.count || basis.maximum <= basis.minimum) {
      const none = el("p", "no-basis", "Keine Vergleichsbasis — zu wenige ähnliche Angebote.");
      wrap.appendChild(none);
      return wrap;
    }

    const span = basis.maximum - basis.minimum;
    const at = (value) => `${Math.min(100, Math.max(0, ((value - basis.minimum) / span) * 100))}%`;
    const offset = Math.round(((basis.amount - basis.median) / basis.median) * 100);
    const verdict = offset <= -5 ? "under" : offset >= 5 ? "over" : "level";
    wrap.className = "ruler";
    wrap.style.setProperty(
      "--verdict-color",
      verdict === "under" ? "var(--under)" : verdict === "over" ? "var(--over)" : "var(--ink-100)"
    );

    const track = el("div", "track");
    [basis.minimum, basis.maximum].forEach((value) => {
      const tick = el("span", "tick");
      tick.style.left = at(value);
      track.appendChild(tick);
    });
    const median = el("span", "tick median");
    median.style.left = at(basis.median);
    track.appendChild(median);
    const marker = el("span", "marker");
    marker.style.left = at(basis.amount);
    track.appendChild(marker);
    wrap.appendChild(track);

    const scale = el("div", "scale");
    const put = (text, left, lead, shift) => {
      const node = el("span", lead ? "lead" : "", text);
      node.style.left = left;
      if (shift) node.style.transform = shift;
      scale.appendChild(node);
    };
    put(String(basis.minimum), "0", false, "none");
    put(`Median ${basis.median}`, at(basis.median), false);
    put(String(basis.maximum), "100%", false, "translateX(-100%)");
    put(String(basis.amount), at(basis.amount), true);
    wrap.appendChild(scale);

    const readout = el("p", "readout");
    const strong = el("b");
    strong.textContent =
      verdict === "level"
        ? "Auf Höhe des Medians"
        : `${Math.abs(offset)} % ${offset < 0 ? "unter" : "über"} dem Median`;
    readout.appendChild(strong);
    readout.append(` von ${basis.count} vergleichbaren Angeboten`);
    const basisText = (find.price_comparison || "").match(/\(([^)]*(?:tutti|facebook)[^)]*)\)/i);
    if (basisText) {
      const span2 = el("span", "basis", ` (${basisText[1]})`);
      readout.appendChild(span2);
    }
    wrap.appendChild(readout);
    return wrap;
  }

  function findCard(find) {
    const card = el("article", "find");
    const top = el("div", "find-top");

    if (find.image) {
      const img = el("img", "thumb");
      img.src = find.image;
      img.alt = "";
      img.loading = "lazy";
      img.addEventListener("error", () => img.replaceWith(el("div", "thumb")));
      top.appendChild(img);
    } else {
      top.appendChild(el("div", "thumb"));
    }

    const mid = el("div");
    const title = el("h4");
    if (find.url) {
      const link = el("a", null, find.title || "Ohne Titel");
      link.href = find.url;
      link.target = "_blank";
      link.rel = "noopener";
      title.appendChild(link);
    } else {
      title.textContent = find.title || "Ohne Titel";
    }
    mid.appendChild(title);

    const sub = el("p", "sub");
    if (find.marketplace) sub.appendChild(el("span", "source", find.marketplace));
    const bits = [find.location, find.condition, find.found_at].filter(Boolean);
    sub.append(` ${bits.join(" · ")}`);
    mid.appendChild(sub);

    if (find.rating) {
      // The pill carries the verdict; the model's sentence is prose and gets
      // its own line, or a long comment turns the chip into a paragraph.
      const labels = {
        1: "Kein Treffer",
        2: "Vielleicht",
        3: "Passt teilweise",
        4: "Guter Fund",
        5: "Sehr guter Fund",
      };
      const verdict = el("span", "verdict");
      verdict.appendChild(el("span", "stars", "★".repeat(find.rating)));
      verdict.append(` ${labels[find.rating] || `Note ${find.rating}`}`);
      mid.appendChild(verdict);
      if (find.ai_comment) mid.appendChild(el("p", "ai-comment", find.ai_comment));
    }
    top.appendChild(mid);

    const price = el("div", "price");
    if (find.converted_price) {
      price.textContent = find.converted_price;
      price.appendChild(el("span", "orig", find.price));
    } else {
      price.textContent = find.price || "—";
    }
    top.appendChild(price);

    card.appendChild(top);
    card.appendChild(ruler(find));
    return card;
  }

  /* ---------- Funde ------------------------------------------------------ */

  async function loadFeed() {
    const feed = $("#feed");
    feed.replaceChildren(el("div", "skeleton"), el("div", "skeleton"));
    try {
      const query = state.hunt ? `?item=${encodeURIComponent(state.hunt)}` : "";
      const body = await api(`/api/found${query}`);
      renderRail(body.items || []);
      if (!body.finds.length) {
        feed.replaceChildren(
          Object.assign(el("div", "state"), {
            innerHTML:
              "<b>Noch keine Funde</b>Sobald eine Jagd etwas findet, steht es hier. " +
              "Über <em>Jetzt suchen</em> läuft sofort ein Durchgang.",
          })
        );
        return;
      }
      feed.replaceChildren(...body.finds.map(findCard));
    } catch (err) {
      feed.replaceChildren(
        Object.assign(el("div", "state err"), { textContent: `Funde nicht ladbar: ${err.message}` })
      );
    }
  }

  function renderRail(items) {
    const rail = $("#hunt-rail");
    rail.replaceChildren();
    const entry = (name, label) => {
      const btn = el("button", "hunt");
      btn.type = "button";
      btn.setAttribute("aria-current", String(state.hunt === name));
      btn.appendChild(el("span", "name", label));
      btn.addEventListener("click", () => {
        state.hunt = name;
        loadFeed();
      });
      return btn;
    };
    rail.appendChild(entry("", "Alle Funde"));
    items.forEach((name) => rail.appendChild(entry(name, name)));
  }

  /* ---------- Sections and forms ----------------------------------------- */

  async function refresh() {
    const [schema, sections] = await Promise.all([api("/api/schema"), api("/api/sections")]);
    state.schema = schema;
    state.sections = sections.sections || [];
  }

  const of = (kind) => state.sections.filter((s) => s.kind === kind);

  function fieldsFor(kind, variants) {
    const byKind = (state.schema && state.schema.kinds && state.schema.kinds[kind]) || {};
    const names = Array.isArray(variants) ? variants : [variants].filter(Boolean);
    const lists = names.map((v) => byKind[v]).filter(Boolean);
    if (!lists.length) return Object.values(byKind)[0] || [];
    // A hunt on several marketplaces may only use options all of them accept,
    // so the form offers the intersection rather than letting the save fail.
    return lists[0].filter((f) => lists.every((list) => list.some((g) => g.name === f.name)));
  }

  function control(field, value) {
    const wrap = el("div", "field");
    wrap.dataset.field = field.name;
    const label = el("label", null, field.name.replace(/_/g, " "));
    if (field.required) label.appendChild(el("span", "req", " *"));
    wrap.appendChild(label);

    let input;
    if (field.type === "boolean") {
      input = el("input");
      input.type = "checkbox";
      input.checked = value === true;
    } else if (field.multiline) {
      input = el("textarea");
      input.value = value == null ? "" : String(value);
    } else if (field.choices && field.choices.length && !field.open_choices && field.type !== "list") {
      input = el("select");
      input.appendChild(el("option", null, ""));
      field.choices.forEach((choice) => {
        const option = el("option", null, choice);
        option.value = choice;
        input.appendChild(option);
      });
      input.value = value == null ? "" : String(value);
    } else {
      input = el("input");
      input.type = field.type === "number" ? "number" : "text";
      input.value = Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);
      if (field.placeholder) input.placeholder = field.placeholder;
    }
    input.name = field.name;
    input.dataset.kind = field.type;
    wrap.appendChild(input);

    const note = [field.help];
    if (field.type === "list") note.push("Mehrere durch Komma trennen.");
    if (field.choices && field.choices.length && (field.open_choices || field.type === "list")) {
      note.push(`Möglich: ${field.choices.slice(0, 8).join(", ")}${field.choices.length > 8 ? " …" : ""}`);
    }
    if (note.filter(Boolean).length) {
      wrap.appendChild(el("p", "field-note", note.filter(Boolean).join(" ")));
    }
    return wrap;
  }

  function readForm(form) {
    const values = {};
    form.querySelectorAll("[name]").forEach((input) => {
      const kind = input.dataset.kind;
      if (kind === "boolean") {
        if (input.checked) values[input.name] = true;
        return;
      }
      const raw = input.value.trim();
      if (!raw) return;
      if (kind === "list") {
        values[input.name] = raw.split(",").map((v) => v.trim()).filter(Boolean);
      } else if (kind === "number") {
        values[input.name] = Number(raw);
      } else {
        values[input.name] = raw;
      }
    });
    return values;
  }

  function openModal(title, hint, build, onSave) {
    const modal = $("#form-modal");
    const form = $("#section-form");
    $("#form-modal-title").textContent = title;
    const hintNode = $("#form-modal-hint");
    hintNode.textContent = hint || "";
    hintNode.hidden = !hint;
    $("#form-error").hidden = true;
    form.replaceChildren();
    build(form);
    modal.classList.remove("hidden");

    const close = () => {
      modal.classList.add("hidden");
      $("#form-save").replaceWith($("#form-save").cloneNode(true));
    };
    $("#form-modal-close").onclick = close;
    $("#form-cancel").onclick = close;
    $(".modal-backdrop").onclick = close;
    $("#form-save").onclick = async () => {
      const errorNode = $("#form-error");
      errorNode.hidden = true;
      form.querySelectorAll(".field").forEach((f) => {
        f.classList.remove("invalid");
        f.querySelectorAll(".field-error").forEach((e) => e.remove());
      });
      try {
        const errors = await onSave(form);
        if (errors && Object.keys(errors).length) {
          Object.entries(errors).forEach(([name, message]) => {
            const target = form.querySelector(`.field[data-field="${name}"]`);
            if (target) {
              target.classList.add("invalid");
              target.appendChild(el("p", "field-error", message));
            } else {
              errorNode.textContent = message;
              errorNode.hidden = false;
            }
          });
          return;
        }
        close();
        await refresh();
        showView(document.querySelector("#nav button[aria-current='true']").dataset.view);
      } catch (err) {
        errorNode.textContent = err.message;
        errorNode.hidden = false;
      }
    };
  }

  /* ---------- Jagden ------------------------------------------------------ */

  function huntEditor(existing) {
    const marketplaces = of("marketplace").map((m) => m.name);
    let chosen = existing
      ? Array.isArray(existing.values.marketplace)
        ? existing.values.marketplace.slice()
        : [existing.values.marketplace || marketplaces[0]].filter(Boolean)
      : marketplaces.slice(0, 1);

    openModal(
      existing ? `Jagd ${existing.name}` : "Neue Jagd",
      "Eine Jagd kann mehrere Marktplätze beobachten. Angeboten werden nur Optionen, die alle gewählten kennen.",
      (form) => {
        if (!existing) {
          const nameField = el("div", "field");
          nameField.dataset.field = "__name";
          nameField.appendChild(el("label", null, "Name der Jagd"));
          const input = el("input");
          input.id = "hunt-name";
          input.placeholder = "gopro";
          nameField.appendChild(input);
          form.appendChild(nameField);
        }

        const pickField = el("div", "field");
        pickField.appendChild(el("span", "field-label", "Wo gesucht wird"));
        const picker = el("div", "picker");
        const body = el("div");
        const paint = () => {
          picker.replaceChildren();
          marketplaces.forEach((name) => {
            const btn = el("button", "pick");
            btn.type = "button";
            btn.setAttribute("aria-pressed", String(chosen.includes(name)));
            btn.appendChild(el("span", "box"));
            btn.append(name);
            btn.addEventListener("click", () => {
              chosen = chosen.includes(name)
                ? chosen.filter((n) => n !== name)
                : chosen.concat(name);
              paint();
              fields();
            });
            picker.appendChild(btn);
          });
        };
        const fields = () => {
          body.replaceChildren();
          fieldsFor("item", chosen).forEach((field) => {
            if (field.name === "marketplace") return;
            body.appendChild(control(field, existing ? existing.values[field.name] : undefined));
          });
        };
        pickField.appendChild(picker);
        form.appendChild(pickField);
        form.appendChild(body);
        paint();
        fields();
      },
      async (form) => {
        const name = existing ? existing.name : ($("#hunt-name")?.value || "").trim();
        if (!name) return { __name: "Bitte einen Namen angeben." };
        if (!chosen.length) return { "": "Mindestens ein Marktplatz muss gewählt sein." };
        const values = readForm(form);
        delete values.__name;
        const payload = { name, variant: chosen.length === 1 ? chosen[0] : chosen, values };
        const res = existing
          ? await api(`/api/sections/item/${encodeURIComponent(name)}`, {
              method: "PUT",
              body: payload,
            })
          : await api("/api/sections/item", { method: "POST", body: payload });
        return res.errors;
      }
    );
  }

  function renderHunts() {
    const list = $("#hunt-list");
    list.replaceChildren();
    const hunts = of("item");
    if (!hunts.length) {
      list.appendChild(
        Object.assign(el("div", "row"), {
          innerHTML: '<div class="who"><b>Noch keine Jagd</b><span>Lege unten die erste an.</span></div>',
        })
      );
      return;
    }
    hunts.forEach((hunt) => {
      const row = el("div", "row");
      const who = el("div", "who");
      who.appendChild(el("b", null, hunt.name));
      const where = Array.isArray(hunt.values.marketplace)
        ? hunt.values.marketplace.join(", ")
        : hunt.values.marketplace || "—";
      const phrases = [].concat(hunt.values.search_phrases || []).join(", ");
      who.appendChild(el("span", null, `${where} · ${phrases || "keine Suchbegriffe"}`));
      row.appendChild(who);
      const edit = el("button", "ghost small", "Bearbeiten");
      edit.addEventListener("click", () => huntEditor(hunt));
      row.appendChild(edit);
      const remove = el("button", "ghost small danger", "Löschen");
      remove.addEventListener("click", async () => {
        if (!confirm(`Jagd ${hunt.name} löschen?`)) return;
        await api(`/api/sections/item/${encodeURIComponent(hunt.name)}`, { method: "DELETE" });
        await refresh();
        renderHunts();
      });
      row.appendChild(remove);
      list.appendChild(row);
    });
  }

  /* ---------- Verbindungen ------------------------------------------------ */

  function sectionEditor(kind, existing, title, hint) {
    const variants = Object.keys((state.schema.kinds && state.schema.kinds[kind]) || {});
    let variant = existing ? existing.variant || variants[0] : variants[0];
    openModal(
      title,
      hint,
      (form) => {
        if (!existing) {
          const nameField = el("div", "field");
          nameField.appendChild(el("label", null, "Name"));
          const input = el("input");
          input.id = "section-name-new";
          nameField.appendChild(input);
          form.appendChild(nameField);
        }
        const body = el("div");
        const paint = () => {
          body.replaceChildren();
          fieldsFor(kind, variant).forEach((field) => {
            body.appendChild(control(field, existing ? existing.values[field.name] : undefined));
          });
        };
        if (variants.length > 1) {
          const choice = el("div", "field");
          choice.appendChild(el("label", null, "Typ"));
          const select = el("select");
          variants.forEach((v) => {
            const option = el("option", null, v);
            option.value = v;
            select.appendChild(option);
          });
          select.value = variant;
          select.addEventListener("change", () => {
            variant = select.value;
            paint();
          });
          choice.appendChild(select);
          form.appendChild(choice);
        }
        form.appendChild(body);
        paint();
      },
      async (form) => {
        const name = existing ? existing.name : ($("#section-name-new")?.value || "").trim();
        if (!name) return { "": "Bitte einen Namen angeben." };
        const payload = { name, variant, values: readForm(form) };
        const res = existing
          ? await api(`/api/sections/${kind}/${encodeURIComponent(name)}`, {
              method: "PUT",
              body: payload,
            })
          : await api(`/api/sections/${kind}`, { method: "POST", body: payload });
        return res.errors;
      }
    );
  }

  function testRow(row, label, run) {
    const button = el("button", "ghost small", label);
    const result = el("p", "result");
    result.hidden = true;
    button.addEventListener("click", async () => {
      button.disabled = true;
      const original = button.textContent;
      button.textContent = "läuft…";
      result.hidden = false;
      result.className = "result";
      result.textContent = "";
      try {
        const res = await run();
        result.className = `result ${res.ok ? "ok" : "bad"}`;
        result.textContent = res.message;
      } catch (err) {
        result.className = "result bad";
        result.textContent = err.message;
      } finally {
        button.disabled = false;
        button.textContent = original;
      }
    });
    row.appendChild(button);
    row.appendChild(result);
  }

  async function renderConnections() {
    const health = await api("/api/health").catch(() => null);
    state.health = health;

    const markets = $("#marketplace-list");
    markets.replaceChildren();
    of("marketplace").forEach((section) => {
      const info = ((health && health.marketplaces) || []).find((m) => m.name === section.name);
      const enabled = section.values.enabled !== false;
      const row = el("div", `row${enabled ? "" : " off"}`);
      const who = el("div", "who");
      who.appendChild(el("b", null, section.name));
      // Only worth saying when it differs from the section name, which it
      // usually does not — "facebook · facebook" tells nobody anything.
      const facts = section.variant && section.variant !== section.name ? [section.variant] : [];
      if (info && info.needs_login) {
        facts.push(info.has_credentials ? "angemeldet" : "kein Login hinterlegt");
      }
      if (!facts.length) facts.push("bereit");
      who.appendChild(el("span", null, facts.join(" · ")));
      row.appendChild(who);

      const toggle = el("button", "switch");
      toggle.type = "button";
      toggle.setAttribute("role", "switch");
      toggle.setAttribute("aria-checked", String(enabled));
      toggle.setAttribute("aria-label", `${section.name} durchsuchen`);
      toggle.addEventListener("click", async () => {
        const next = toggle.getAttribute("aria-checked") !== "true";
        toggle.setAttribute("aria-checked", String(next));
        const values = { ...section.values, enabled: next };
        await api(`/api/sections/marketplace/${encodeURIComponent(section.name)}`, {
          method: "PUT",
          body: { name: section.name, variant: section.variant, values },
        });
        await refresh();
        renderConnections();
      });
      row.appendChild(toggle);

      const edit = el("button", "ghost small", "Bearbeiten");
      edit.addEventListener("click", () =>
        sectionEditor("marketplace", section, `Marktplatz ${section.name}`, "")
      );
      row.appendChild(edit);
      markets.appendChild(row);
    });

    const users = $("#user-list");
    users.replaceChildren();
    of("user").forEach((section) => {
      const info = ((health && health.users) || []).find((u) => u.name === section.name);
      const row = el("div", "row");
      const who = el("div", "who");
      who.appendChild(el("b", null, section.name));
      who.appendChild(
        el("span", null, (info && info.methods.length ? info.methods.join(", ") : "kein Weg eingerichtet"))
      );
      row.appendChild(who);
      testRow(row, "Testnachricht", () =>
        api("/api/test/notification", { method: "POST", body: { user: section.name } })
      );
      const edit = el("button", "ghost small", "Bearbeiten");
      edit.addEventListener("click", () => sectionEditor("user", section, `Benutzer ${section.name}`, ""));
      row.appendChild(edit);
      users.appendChild(row);
    });
    if (!of("user").length) {
      users.appendChild(
        Object.assign(el("div", "row"), {
          innerHTML: '<div class="who"><b>Kein Empfänger</b><span>Ohne Benutzer gibt es keine Benachrichtigungen.</span></div>',
        })
      );
    }

    const ais = $("#ai-list");
    ais.replaceChildren();
    of("ai").forEach((section) => {
      const row = el("div", "row");
      const who = el("div", "who");
      who.appendChild(el("b", null, section.name));
      who.appendChild(
        el("span", null, [section.values.model, section.values.base_url].filter(Boolean).join(" @ ") || "—")
      );
      row.appendChild(who);
      testRow(row, "KI prüfen", () =>
        api("/api/test/ai", { method: "POST", body: { ai: section.name } })
      );
      const edit = el("button", "ghost small", "Bearbeiten");
      edit.addEventListener("click", () => sectionEditor("ai", section, `KI ${section.name}`, ""));
      row.appendChild(edit);
      ais.appendChild(row);
    });
    if (!of("ai").length) {
      const row = el("div", "row");
      row.innerHTML =
        '<div class="who"><b>Keine KI eingerichtet</b><span>Funde kommen dann unbewertet durch.</span></div>';
      const add = el("button", "ghost small", "Einrichten");
      add.addEventListener("click", () => sectionEditor("ai", null, "KI-Dienst", ""));
      row.appendChild(add);
      ais.appendChild(row);
    }
  }

  /* ---------- Einstellungen ----------------------------------------------- */

  function renderSettings() {
    const host = $("#monitor-settings");
    host.replaceChildren();
    const monitor = state.sections.find((s) => s.kind === "monitor") || { values: {} };
    const button = el("button", "ghost small", "Bearbeiten");
    const who = el("div", "who");
    who.appendChild(el("b", null, `Anzeigewährung: ${monitor.values.currency || "nicht gesetzt"}`));
    who.appendChild(
      el(
        "span",
        null,
        monitor.values.fixer_api_key
          ? "fixer.io-Schlüssel hinterlegt"
          : "ohne Schlüssel — Kurse von Frankfurter"
      )
    );
    const row = el("div", "row");
    row.style.borderTop = "0";
    row.appendChild(who);
    button.addEventListener("click", () =>
      sectionEditor(
        "monitor",
        state.sections.find((s) => s.kind === "monitor") || { name: "monitor", values: {}, variant: null },
        "Anzeige und Wechselkurse",
        ""
      )
    );
    row.appendChild(button);
    host.appendChild(row);
  }

  /* ---------- Boot -------------------------------------------------------- */

  function wireChrome() {
    document.querySelectorAll("#nav button").forEach((btn) => {
      btn.addEventListener("click", () => showView(btn.dataset.view));
    });
    $("#rail-new")?.addEventListener("click", () => huntEditor(null));
    $("#hunt-new")?.addEventListener("click", () => huntEditor(null));

    const drawer = $("#drawer");
    const toggle = $("#drawer-toggle");
    toggle?.addEventListener("click", () => {
      const open = drawer.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = `${open ? "▾" : "▸"} Protokoll`;
    });

    $("#cache-clear")?.addEventListener("click", async () => {
      if (!confirm("Zwischenspeicher leeren? Der nächste Lauf meldet alles noch einmal.")) return;
      const result = $("#cache-result");
      result.hidden = false;
      result.textContent = "läuft…";
      try {
        const res = await api("/api/cache/clear", { method: "POST", body: { scope: "all" } });
        result.className = `result ${res.ok ? "ok" : "bad"}`;
        result.textContent = res.message;
      } catch (err) {
        result.className = "result bad";
        result.textContent = err.message;
      }
    });
  }

  async function boot() {
    wireSetup();
    if (await setupNeeded()) {
      $("#setup-screen").classList.remove("hidden");
      $("#login-screen").classList.add("hidden");
      $("#app").classList.add("hidden");
      return;
    }
    wireChrome();
    // app.js decides between login and app; only load our data once it is in.
    const ready = setInterval(async () => {
      if ($("#app").classList.contains("hidden")) return;
      clearInterval(ready);
      try {
        await refresh();
        showView("finds");
      } catch (err) {
        console.error("UI konnte nicht laden:", err);
      }
    }, 150);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
