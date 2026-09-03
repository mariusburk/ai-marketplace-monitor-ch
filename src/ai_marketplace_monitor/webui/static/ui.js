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

  /* ---------- Einrichten -------------------------------------------------- */

  // A monitor needs a marketplace, a recipient and a hunt. Set up through the
  // UI they arrive one at a time, so until all three exist the finds view has
  // nothing to show — and says what is missing instead of staying blank.
  const ONBOARD = [
    {
      kind: "marketplace",
      title: "Marktplatz verbinden",
      why: "Wo gesucht wird. tutti braucht kein Konto, Facebook einen Login.",
      cta: "Marktplatz anlegen",
      open: () =>
        sectionEditor(
          "marketplace",
          null,
          "Neuer Marktplatz",
          "Der Typ bestimmt, welche Optionen es gibt. Der Name darf frei sein.",
          (state.schema.marketplaces || []).find(
            (name) => !of("marketplace").some((s) => (s.variant || s.name) === name)
          )
        ),
    },
    {
      kind: "user",
      title: "Empfänger anlegen",
      why: "Wohin die Treffer gemeldet werden — Pushbullet, Telegram, ntfy oder E-Mail.",
      cta: "Empfänger anlegen",
      open: () => sectionEditor("user", null, "Neuer Empfänger", ""),
    },
    {
      kind: "item",
      title: "Erste Jagd anlegen",
      why: "Wonach gesucht wird, in welchem Preisrahmen, ab welcher Note.",
      cta: "Jagd anlegen",
      open: () => huntEditor(null),
    },
  ];

  function renderOnboarding() {
    const panel = $("#onboard");
    const host = $("#onboard-steps");
    if (!panel || !host) return false;
    const missing = ONBOARD.filter((step) => !of(step.kind).length);
    panel.hidden = !missing.length;
    if (!missing.length) return false;
    host.replaceChildren();
    ONBOARD.forEach((step, index) => {
      const done = !!of(step.kind).length;
      const row = el("div", `row onboard-step${done ? " done" : ""}`);
      row.appendChild(el("span", "num", done ? "✓" : String(index + 1)));
      const who = el("div", "who");
      who.appendChild(el("b", null, step.title));
      who.appendChild(
        el("span", null, done ? of(step.kind).map((s) => s.name).join(", ") : step.why)
      );
      row.appendChild(who);
      if (!done) {
        const go = el("button", missing[0] === step ? "primary small" : "ghost small", step.cta);
        go.addEventListener("click", step.open);
        row.appendChild(go);
      }
      host.appendChild(row);
    });
    return true;
  }

  /* ---------- Funde ------------------------------------------------------ */

  async function loadFeed() {
    renderOnboarding();
    const feed = $("#feed");
    feed.replaceChildren(el("div", "skeleton"), el("div", "skeleton"));
    try {
      const query = state.hunt ? `?item=${encodeURIComponent(state.hunt)}` : "";
      const body = await api(`/api/found${query}`);
      // From the config, not from the response: a hunt that has not found
      // anything yet is still a hunt, and filtering to one hunt used to leave
      // the rail with no way back to the others.
      renderRail(
        of("item").map((s) => s.name),
        new Set(body.items || [])
      );
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

  function renderRail(hunts, withFinds) {
    const rail = $("#hunt-rail");
    rail.replaceChildren();
    const entry = (name, label, quiet) => {
      const btn = el("button", `hunt${quiet ? " quiet" : ""}`);
      btn.type = "button";
      btn.setAttribute("aria-current", String(state.hunt === name));
      btn.appendChild(el("span", "name", label));
      if (quiet) btn.appendChild(el("span", "note", "noch keine Funde"));
      btn.addEventListener("click", () => {
        state.hunt = name;
        loadFeed();
      });
      return btn;
    };
    rail.appendChild(entry("", "Alle Funde"));
    hunts.forEach((name) => rail.appendChild(entry(name, name, !withFinds.has(name))));
  }

  /* ---------- Sections and forms ----------------------------------------- */

  async function refresh() {
    const [schema, sections] = await Promise.all([api("/api/schema"), api("/api/sections")]);
    state.schema = schema;
    state.sections = sections.sections || [];
  }

  const of = (kind) => state.sections.filter((s) => s.kind === kind);

  function stepsFor(kind, variants) {
    const byKind = (state.schema && state.schema.kinds && state.schema.kinds[kind]) || {};
    const names = Array.isArray(variants) ? variants : [variants].filter(Boolean);
    const lists = names.map((v) => byKind[v]).filter(Boolean);
    if (!lists.length) return Object.values(byKind)[0] || [];
    if (lists.length === 1) return lists[0];
    // The union, not the intersection. Each marketplace narrows differently —
    // facebook by city, tutti by canton — and picking both used to make every
    // one of those fields vanish, because none was common to all. The loader
    // hands each marketplace only the options it knows (`Config._options_for`),
    // so both can stand here; a field only some accept says which.
    const merged = [];
    lists.forEach((steps, index) => {
      const market = names[index];
      steps.forEach((step) => {
        let target = merged.find((s) => s.id === step.id);
        if (!target) {
          target = { ...step, fields: [], advanced: [] };
          merged.push(target);
        }
        ["fields", "advanced"].forEach((group) => {
          step[group].forEach((f) => {
            const seen = target[group].find((g) => g.name === f.name);
            if (seen) {
              seen.markets.push(market);
              return;
            }
            target[group].push({ ...f, markets: [market] });
          });
        });
      });
    });
    // Say which marketplace an option belongs to, but only where it matters.
    merged.forEach((step) => {
      ["fields", "advanced"].forEach((group) => {
        step[group].forEach((f) => {
          if (f.markets.length < names.length) f.only = f.markets.join(", ");
        });
      });
    });
    return merged.filter(
      (step) => step.fields.length || step.advanced.length || UI_STEPS.has(step.id)
    );
  }

  // Steps the UI fills itself: the marketplace picker and the channel picker
  // have no config field behind them, so they arrive empty and stay.
  const UI_STEPS = new Set(["where", "channels"]);

  /* ---------- Controls ---------------------------------------------------- */
  //
  // One builder per control. Each returns { node, read() }, where read() gives
  // back an object of field names, so one control can own several fields — the
  // places editor writes four at once, because Facebook zips them positionally
  // and a shorter list silently truncates the search.

  // "(a OR b) AND c"  <->  [["a","b"],["c"]]
  //
  // Returns null when the expression is not that shape — a NOT, a nested
  // parenthesis, an OR of ANDs. Those keep their text box rather than being
  // silently rewritten into something that means something else.
  function parseTerms(text) {
    const trimmed = (text || "").trim();
    if (!trimmed) return [];
    if (/\bNOT\b/.test(trimmed)) return null;
    const groups = [];
    for (const part of splitTop(trimmed, "AND")) {
      let chunk = part.trim();
      const wrapped = /^\((.*)\)$/s.exec(chunk);
      if (wrapped && splitTop(wrapped[1], "AND").length === 1) chunk = wrapped[1].trim();
      if (chunk.includes("(") || chunk.includes(")")) return null;
      const words = splitTop(chunk, "OR").map(unquote);
      if (words.some((w) => !w || /\b(AND|OR|NOT)\b/.test(w))) return null;
      groups.push(words);
    }
    return groups;
  }

  // Split on an operator only where the parentheses are balanced.
  function splitTop(text, op) {
    const out = [];
    let depth = 0;
    let at = 0;
    const pattern = new RegExp(`\\b${op}\\b`, "g");
    let match;
    for (let i = 0; i < text.length; i += 1) {
      if (text[i] === "(") depth += 1;
      else if (text[i] === ")") depth -= 1;
    }
    if (depth !== 0) return [text];
    depth = 0;
    let quote = null;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (quote) {
        if (ch === quote) quote = null;
        continue;
      }
      if (ch === "'" || ch === '"') quote = ch;
      else if (ch === "(") depth += 1;
      else if (ch === ")") depth -= 1;
      else if (depth === 0) {
        pattern.lastIndex = i;
        match = pattern.exec(text);
        if (match && match.index === i) {
          out.push(text.slice(at, i));
          i = pattern.lastIndex - 1;
          at = pattern.lastIndex;
        }
      }
    }
    out.push(text.slice(at));
    return out.map((part) => part.trim()).filter((part) => part.length);
  }

  const unquote = (word) => word.trim().replace(/^['"](.*)['"]$/s, "$1").trim();

  // A word with a space has to be quoted or the parser reads it as two.
  const quoteWord = (word) => (/\s/.test(word) ? `'${word.replace(/'/g, "")}'` : word);

  function buildTerms(groups) {
    const filled = groups.map((group) => group.filter(Boolean)).filter((group) => group.length);
    if (!filled.length) return undefined;
    const parts = filled.map((group) =>
      group.length === 1
        ? quoteWord(group[0])
        : `(${group.map(quoteWord).join(" OR ")})`
    );
    return parts.length === 1 ? parts[0] : parts.join(" AND ");
  }

  const asList = (value) =>
    value == null ? [] : Array.isArray(value) ? value.slice() : [value];

  function labelFor(field) {
    const label = el("label", null, field.label || field.name.replace(/_/g, " "));
    if (field.required) label.appendChild(el("span", "req", " *"));
    // On a hunt spanning several marketplaces, an option only one of them
    // understands would otherwise look like it applied to all.
    if (field.only) label.appendChild(el("span", "only", ` nur ${field.only}`));
    return label;
  }

  function notesFor(field, suppressDefault) {
    const out = [];
    const help = [field.help];
    if (field.type === "list" && ["text", "combo"].includes(field.control)) {
      help.push("Mehrere durch Komma trennen.");
    }
    if (help.filter(Boolean).length) {
      out.push(el("p", "field-note", help.filter(Boolean).join(" ")));
    }
    // A select carries its default in the empty option, so repeating it
    // underneath says the same thing twice. Chips have no empty option.
    if (field.default_note && !suppressDefault) {
      out.push(el("p", "default-note", field.default_note));
    }
    return out;
  }

  const CONTROLS = {
    checkbox(field, value) {
      const box = el("input");
      box.type = "checkbox";
      box.checked = value === undefined || value === null ? field.on_when_unset : value === true;
      const line = el("label", "check-line");
      line.appendChild(box);
      line.append(field.label || field.name);
      // Written either way: several of these default to on when absent, so
      // omitting an unticked box made it impossible to switch anything off.
      return { node: line, read: () => ({ [field.name]: box.checked }), skipLabel: true };
    },

    textarea(field, value) {
      const area = el("textarea");
      area.value = value == null ? "" : String(value);
      if (field.placeholder) area.placeholder = field.placeholder;
      return { node: area, read: () => ({ [field.name]: area.value.trim() || undefined }) };
    },

    select(field, value) {
      const select = el("select");
      const blank = el("option", null, field.default_note || "— nicht gesetzt —");
      blank.value = "";
      select.appendChild(blank);
      field.choices.forEach((choice) => {
        const option = el("option", null, choice);
        option.value = choice;
        select.appendChild(option);
      });
      select.value = value == null ? "" : String(Array.isArray(value) ? value[0] : value);
      return {
        node: select,
        read: () => ({ [field.name]: select.value || undefined }),
        quiet: true,
      };
    },

    combo(field, value) {
      const input = el("input");
      const list = el("datalist");
      list.id = `dl-${field.name}-${Math.random().toString(36).slice(2, 8)}`;
      field.choices.forEach((choice) => {
        const option = el("option");
        option.value = choice;
        list.appendChild(option);
      });
      input.setAttribute("list", list.id);
      input.value = asList(value).join(", ");
      if (field.placeholder) input.placeholder = field.placeholder;
      const wrap = el("div");
      wrap.append(input, list);
      return {
        node: wrap,
        read: () => {
          const raw = input.value.trim();
          if (!raw) return { [field.name]: undefined };
          return {
            [field.name]:
              field.type === "list" ? raw.split(",").map((v) => v.trim()).filter(Boolean) : raw,
          };
        },
      };
    },

    multi(field, value) {
      let picked = asList(value).map(String);
      const box = el("div", "chips");
      const paint = () => {
        box.replaceChildren();
        field.choices.forEach((choice) => {
          const chip = el("button", "chip-pick");
          chip.type = "button";
          chip.textContent = (field.labels && field.labels[choice]) || choice;
          chip.setAttribute("aria-pressed", String(picked.includes(choice)));
          chip.addEventListener("click", () => {
            picked = picked.includes(choice)
              ? picked.filter((c) => c !== choice)
              : picked.concat(choice);
            paint();
          });
          box.appendChild(chip);
        });
      };
      paint();
      return {
        node: box,
        read: () => ({
          [field.name]:
            picked.length === 0
              ? undefined
              : field.type === "list"
              ? picked.slice()
              : picked[0],
        }),
      };
    },

    reference(field, value) {
      let picked = asList(value).map(String);
      const available = of(field.references).map((s) => s.name);
      const box = el("div", "chips");
      if (!available.length) {
        const label = { user: "Empfänger", ai: "KI-Dienste", notification: "Wege" }[
          field.references
        ];
        box.appendChild(el("p", "default-note", `Noch keine ${label} angelegt.`));
      }
      const paint = () => {
        box.replaceChildren();
        available.forEach((name) => {
          const chip = el("button", "chip-pick");
          chip.type = "button";
          chip.textContent = name;
          chip.setAttribute("aria-pressed", String(picked.includes(name)));
          chip.addEventListener("click", () => {
            picked = picked.includes(name) ? picked.filter((n) => n !== name) : picked.concat(name);
            paint();
          });
          box.appendChild(chip);
        });
      };
      if (available.length) paint();
      return {
        node: box,
        read: () => ({ [field.name]: picked.length ? picked.slice() : undefined }),
      };
    },

    rating(field, value) {
      let picked = asList(value)[0];
      const box = el("div", "segments");
      const paint = () => {
        box.replaceChildren();
        [null, 1, 2, 3, 4, 5].forEach((score) => {
          const seg = el("button", "segment");
          seg.type = "button";
          seg.textContent = score === null ? "Standard" : String(score);
          seg.setAttribute("aria-pressed", String(String(picked ?? "") === String(score ?? "")));
          seg.addEventListener("click", () => {
            picked = score === null ? undefined : score;
            paint();
          });
          box.appendChild(seg);
        });
      };
      paint();
      return {
        node: box,
        read: () => ({ [field.name]: picked == null ? undefined : [Number(picked)] }),
      };
    },

    duration(field, value) {
      // Typed `int` in the dataclass, but the validators run a string through
      // convert_to_seconds first — so "30m" is legal and a number box was not
      // just clumsy, it could not express what the field accepts.
      const UNITS = [
        ["m", "Minuten", 60],
        ["h", "Stunden", 3600],
        ["d", "Tage", 86400],
      ];
      const amount = el("input");
      amount.type = "number";
      amount.min = "1";
      const unit = el("select");
      UNITS.forEach(([suffix, name]) => {
        const option = el("option", null, name);
        option.value = suffix;
        unit.appendChild(option);
      });
      const text = value == null ? "" : String(value);
      const parsed = /^(\d+)\s*([mhd])$/.exec(text);
      if (parsed) {
        amount.value = parsed[1];
        unit.value = parsed[2];
      } else if (/^\d+$/.test(text)) {
        // seconds from a hand-edited file: show them in the largest unit that
        // divides evenly, so the number stays the same number
        const seconds = Number(text);
        const [suffix, , size] = [...UNITS].reverse().find(([, , s]) => seconds % s === 0) || UNITS[0];
        amount.value = String(seconds / size);
        unit.value = suffix;
      } else {
        unit.value = "m";
      }
      const row = el("div", "pair");
      row.append(amount, unit);
      return {
        node: row,
        read: () => ({
          [field.name]: amount.value.trim() ? `${Number(amount.value)}${unit.value}` : undefined,
        }),
      };
    },

    money(field, value) {
      const text = value == null ? "" : String(value);
      const parsed = /^\s*([\d.,]+)\s*([A-Za-z]{3})?\s*$/.exec(text);
      const amount = el("input");
      amount.type = "number";
      amount.min = "0";
      amount.value = parsed ? parsed[1] : "";
      amount.placeholder = field.placeholder || "";
      const currency = el("input");
      currency.className = "currency";
      currency.maxLength = 3;
      currency.placeholder = "CHF";
      currency.value = parsed && parsed[2] ? parsed[2].toUpperCase() : "";
      const row = el("div", "pair");
      row.append(amount, currency);
      return {
        node: row,
        read: () => {
          if (!amount.value.trim()) return { [field.name]: undefined };
          const unit = currency.value.trim().toUpperCase();
          return { [field.name]: unit ? `${amount.value.trim()} ${unit}` : amount.value.trim() };
        },
      };
    },

    locations(field, value, values) {
      // facebook.py zips search_city, city_name, radius and currency; zip stops
      // at the shortest, so three cities and one currency searches one city.
      // One row per place makes the four lists the same length by construction.
      const members = ["city_name", "radius", "currency"];
      const start = asList(value).map((city, index) => ({
        search_city: city,
        city_name: asList(values.city_name)[index] || "",
        radius: asList(values.radius)[index] ?? "",
        currency: asList(values.currency)[index] || "",
      }));
      let rows = start.length ? start : [{ search_city: "", city_name: "", radius: "", currency: "" }];
      const box = el("div", "rows");
      const paint = () => {
        box.replaceChildren();
        rows.forEach((row, index) => {
          const line = el("div", "row-edit");
          const cell = (key, placeholder, type) => {
            const input = el("input");
            if (type) input.type = type;
            input.placeholder = placeholder;
            input.value = row[key] == null ? "" : String(row[key]);
            input.addEventListener("input", () => {
              row[key] = input.value;
            });
            return input;
          };
          line.append(
            cell("search_city", "zurich"),
            cell("city_name", "Zürich"),
            cell("radius", "Umkreis", "number"),
            cell("currency", "CHF")
          );
          const drop = el("button", "ghost small danger", "×");
          drop.type = "button";
          drop.title = "Ort entfernen";
          drop.addEventListener("click", () => {
            rows.splice(index, 1);
            if (!rows.length) rows.push({ search_city: "", city_name: "", radius: "", currency: "" });
            paint();
          });
          line.appendChild(drop);
          box.appendChild(line);
        });
        const add = el("button", "ghost small", "+ Ort");
        add.type = "button";
        add.addEventListener("click", () => {
          rows.push({ search_city: "", city_name: "", radius: "", currency: "" });
          paint();
        });
        box.appendChild(add);
      };
      paint();
      const head = el("div", "row-head");
      ["Stadt in der URL", "Angezeigter Name", "Umkreis (Meilen)", "Währung"].forEach((title) =>
        head.appendChild(el("span", null, title))
      );
      const wrap = el("div");
      wrap.append(head, box);
      return {
        node: wrap,
        read: () => {
          const filled = rows.filter((row) => String(row.search_city || "").trim());
          if (!filled.length) return { search_city: undefined };
          const out = { search_city: filled.map((row) => row.search_city.trim()) };
          members.forEach((key) => {
            const column = filled.map((row) => String(row[key] ?? "").trim());
            // all four lists must stay the same length, so a column is written
            // whole or not at all
            out[key] = column.some(Boolean)
              ? column.map((cell) => (key === "radius" ? Number(cell) || 0 : cell))
              : undefined;
          });
          return out;
        },
      };
    },

    times(field, value) {
      let rows = asList(value).map(String);
      if (!rows.length) rows = [""];
      const box = el("div", "rows");
      const paint = () => {
        box.replaceChildren();
        rows.forEach((entry, index) => {
          const line = el("div", "row-edit narrow");
          const input = el("input");
          input.placeholder = "08:30";
          input.value = entry;
          input.addEventListener("input", () => {
            rows[index] = input.value;
          });
          const drop = el("button", "ghost small danger", "×");
          drop.type = "button";
          drop.addEventListener("click", () => {
            rows.splice(index, 1);
            if (!rows.length) rows.push("");
            paint();
          });
          line.append(input, drop);
          box.appendChild(line);
        });
        const add = el("button", "ghost small", "+ Uhrzeit");
        add.type = "button";
        add.addEventListener("click", () => {
          rows.push("");
          paint();
        });
        box.appendChild(add);
      };
      paint();
      return {
        node: box,
        read: () => {
          const filled = rows.map((r) => r.trim()).filter(Boolean);
          return { [field.name]: filled.length ? filled : undefined };
        },
      };
    },

    // A tag box: words in, words out, no syntax to learn.
    words(field, value) {
      let words = asList(value)
        .join(", ")
        .split(",")
        .map((w) => w.trim())
        .filter(Boolean);
      const box = el("div", "tagbox");
      const paint = () => {
        box.replaceChildren();
        words.forEach((word, index) => {
          const tag = el("span", "tag", word);
          const drop = el("button", "tag-x", "×");
          drop.type = "button";
          drop.title = `${word} entfernen`;
          drop.addEventListener("click", () => {
            words.splice(index, 1);
            paint();
          });
          tag.appendChild(drop);
          box.appendChild(tag);
        });
        const input = el("input", "tag-input");
        input.placeholder = words.length ? "" : field.placeholder || "Wort eingeben";
        const commit = (refocus) => {
          // A repaint moves the input out of the DOM, which fires blur — and a
          // blur that repaints again lands on a node that is already gone.
          if (!input.isConnected) return;
          const raw = input.value.trim().replace(/,$/, "");
          if (!raw) return;
          if (!words.includes(raw)) words.push(raw);
          paint();
          if (refocus) box.querySelector(".tag-input").focus();
        };
        input.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === ",") {
            event.preventDefault();
            commit(true);
          } else if (event.key === "Backspace" && !input.value && words.length) {
            words.pop();
            paint();
            box.querySelector(".tag-input").focus();
          }
        });
        input.addEventListener("blur", () => commit(false));
        box.appendChild(input);
      };
      paint();
      return {
        node: box,
        read: () => ({ [field.name]: words.length ? words.slice() : undefined }),
      };
    },

    // Keywords, without asking anyone to write a boolean expression.
    //
    // The stored value is one: `is_substring` parses AND / OR / NOT with
    // pyparsing. Nearly every real filter has the same shape — a few groups
    // that must all match, each satisfied by any one of its words — so that is
    // what this edits. Anything more intricate keeps its text box, because
    // rewriting an expression nobody can see would be worse than not offering
    // the builder at all.
    terms(field, value) {
      const text = Array.isArray(value) ? value.join(" AND ") : value == null ? "" : String(value);
      const parsed = parseTerms(text);
      if (parsed === null) {
        const raw = el("input");
        raw.value = text;
        const wrap = el("div");
        wrap.append(
          raw,
          el(
            "p",
            "default-note",
            "Dieser Ausdruck ist für den Baukasten zu verschachtelt und bleibt, wie er ist."
          )
        );
        return {
          node: wrap,
          read: () => ({ [field.name]: raw.value.trim() || undefined }),
        };
      }

      let groups = parsed.length ? parsed : [[]];
      const box = el("div", "groups");
      const paint = () => {
        box.replaceChildren();
        groups.forEach((group, index) => {
          const row = el("div", "group");
          if (index > 0) row.appendChild(el("span", "joiner", "und"));
          const inner = el("div", "group-body");
          const tags = el("div", "tagbox");
          const drawTags = () => {
            tags.replaceChildren();
            group.forEach((word, at) => {
              const tag = el("span", "tag", word);
              const drop = el("button", "tag-x", "×");
              drop.type = "button";
              drop.addEventListener("click", () => {
                group.splice(at, 1);
                drawTags();
              });
              tag.appendChild(drop);
              tags.appendChild(tag);
              if (at < group.length - 1) tags.appendChild(el("span", "or", "oder"));
            });
            const input = el("input", "tag-input");
            input.placeholder = group.length ? "oder …" : field.placeholder || "gopro";
            const commit = (refocus) => {
              if (!input.isConnected) return;
              const raw = input.value.trim().replace(/,$/, "");
              if (!raw || group.includes(raw)) return;
              group.push(raw);
              drawTags();
              if (refocus) tags.querySelector(".tag-input").focus();
            };
            input.addEventListener("keydown", (event) => {
              if (event.key === "Enter" || event.key === ",") {
                event.preventDefault();
                commit(true);
              } else if (event.key === "Backspace" && !input.value && group.length) {
                group.pop();
                drawTags();
                tags.querySelector(".tag-input").focus();
              }
            });
            input.addEventListener("blur", () => commit(false));
            tags.appendChild(input);
          };
          drawTags();
          inner.appendChild(tags);
          row.appendChild(inner);
          if (groups.length > 1) {
            const drop = el("button", "ghost small danger", "×");
            drop.type = "button";
            drop.title = "Zeile entfernen";
            drop.addEventListener("click", () => {
              groups.splice(index, 1);
              if (!groups.length) groups = [[]];
              paint();
            });
            row.appendChild(drop);
          }
          box.appendChild(row);
        });
        const add = el("button", "ghost small", "+ und …");
        add.type = "button";
        add.title = "Eine weitere Bedingung, die zusätzlich zutreffen muss";
        add.addEventListener("click", () => {
          groups.push([]);
          paint();
        });
        box.appendChild(add);
      };
      paint();
      return {
        node: box,
        read: () => ({ [field.name]: buildTerms(groups) }),
      };
    },

    // An address with a "does it answer?" button. Its answer feeds the model
    // list next to it, which is the whole point: no typing a model name from
    // memory and finding out an hour later that it was a typo.
    endpoint(field, value) {
      const input = el("input");
      input.value = value == null ? "" : String(value);
      if (field.placeholder) input.placeholder = field.placeholder;
      const probe = el("button", "ghost small", "Verbindung prüfen");
      probe.type = "button";
      const result = el("p", "result");
      result.hidden = true;
      probe.addEventListener("click", async () => {
        result.hidden = false;
        result.className = "result";
        result.textContent = "prüfe…";
        probe.disabled = true;
        try {
          const res = await api("/api/test/ollama", {
            method: "POST",
            body: { base_url: input.value.trim() },
          });
          result.className = `result ${res.ok ? "ok" : "bad"}`;
          result.textContent = res.message;
          const models = (res.detail && res.detail.models) || [];
          if (res.ok && res.detail && res.detail.base_url) input.value = res.detail.base_url;
          state.models = models;
          document.dispatchEvent(new CustomEvent("aimm:models", { detail: models }));
        } catch (err) {
          result.className = "result bad";
          result.textContent = err.message;
        } finally {
          probe.disabled = false;
        }
      });
      const row = el("div", "pair");
      row.append(input, probe);
      const wrap = el("div");
      wrap.append(row, result);
      return {
        node: wrap,
        read: () => ({ [field.name]: input.value.trim() || undefined }),
      };
    },

    "model-list"(field, value) {
      const current = value == null ? "" : String(value);
      const host = el("div");
      const draw = (models) => {
        host.replaceChildren();
        if (!models.length) {
          const input = el("input");
          input.value = current;
          input.placeholder = "qwen2.5:7b";
          host.appendChild(input);
          host._value = () => input.value.trim();
          return;
        }
        const select = el("select");
        if (!models.includes(current)) {
          const blank = el("option", null, "— Modell wählen —");
          blank.value = "";
          select.appendChild(blank);
        }
        models.forEach((model) => {
          const option = el("option", null, model);
          option.value = model;
          select.appendChild(option);
        });
        select.value = models.includes(current) ? current : "";
        host.appendChild(select);
        host.appendChild(el("p", "default-note", `${models.length} auf dem Server gefunden.`));
        host._value = () => select.value;
      };
      draw(state.models || []);
      const listen = (event) => draw(event.detail || []);
      document.addEventListener("aimm:models", listen);
      return {
        node: host,
        read: () => ({ [field.name]: (host._value && host._value()) || undefined }),
      };
    },

    number(field, value) {
      const input = el("input");
      input.type = "number";
      input.value = value == null ? "" : String(value);
      if (field.placeholder) input.placeholder = field.placeholder;
      return {
        node: input,
        read: () => ({
          [field.name]: input.value.trim() ? Number(input.value) : undefined,
        }),
      };
    },

    text(field, value) {
      const input = el("input");
      if (field.secret) input.type = "password";
      input.value = Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);
      if (field.placeholder) input.placeholder = field.placeholder;
      return {
        node: input,
        read: () => {
          const raw = input.value.trim();
          if (!raw) return { [field.name]: undefined };
          return {
            [field.name]:
              field.type === "list"
                ? raw.split(",").map((v) => v.trim()).filter(Boolean)
                : field.type === "number"
                ? Number(raw)
                : raw,
          };
        },
      };
    },
  };

  function control(field, values) {
    const build = CONTROLS[field.control] || CONTROLS.text;
    const made = build(field, values ? values[field.name] : undefined, values || {});
    const wrap = el("div", "field");
    wrap.dataset.field = field.name;
    (field.composite || []).forEach((member) => {
      wrap.dataset[`owns${member}`] = "1";
    });
    if (!made.skipLabel) wrap.appendChild(labelFor(field));
    wrap.appendChild(made.node);
    notesFor(field, made.quiet).forEach((note) => wrap.appendChild(note));
    if (field.note) wrap.appendChild(el("p", "caveat", field.note));
    wrap.dataset.control = field.control;
    wrap._read = made.read;
    return wrap;
  }

  /* ---------- The form itself --------------------------------------------- */
  //
  // One definition, two shapes. Creating something walks the steps one at a
  // time, because a person meeting a marketplace for the first time should be
  // asked one thing at a time and told where they are. Editing shows every
  // step at once as disclosures, because someone who came back to change the
  // canton should not have to click through five screens to reach it.

  function renderStep(host, step, values, hidden) {
    const wanted = (list) => list.filter((f) => !hidden || !hidden.has(f.name));
    const main = wanted(step.fields);
    const extra = wanted(step.advanced);
    main.forEach((f) => host.appendChild(control(f, values)));
    if (!extra.length) return;
    const more = el("details", "more");
    more.appendChild(el("summary", null, `Feinheiten (${extra.length})`));
    extra.forEach((f) => more.appendChild(control(f, values)));
    host.appendChild(more);
  }

  function readForm(form) {
    const values = {};
    form.querySelectorAll(".field").forEach((wrap) => {
      if (!wrap._read || wrap.dataset.skip === "1") return;
      Object.entries(wrap._read()).forEach(([name, value]) => {
        if (value !== undefined) values[name] = value;
      });
    });
    return values;
  }

  /**
   * Paint errors onto the fields they belong to.
   *
   * `owns` decides which errors this screen may report. While stepping through
   * a wizard a missing search phrase must not block the step that asks for the
   * marketplace — but nor may it be reported *there*, as a banner about a
   * field three screens back, which is what judging by "is it in the DOM"
   * produced. An error with no field at all is always shown: it has nowhere
   * else to go, and swallowing it left the form looking like the button had
   * not worked.
   */
  function showErrors(form, errors, owns) {
    form.querySelectorAll(".field").forEach((f) => {
      f.classList.remove("invalid");
      f.querySelectorAll(".field-error").forEach((e) => e.remove());
    });
    const banner = $("#form-error");
    banner.hidden = true;
    let shown = 0;
    let first = null;
    Object.entries(errors || {}).forEach(([name, message]) => {
      if (name && owns && !owns(name)) return;
      const target = form.querySelector(`.field[data-field="${name}"]`);
      shown += 1;
      if (target) {
        target.classList.add("invalid");
        target.appendChild(el("p", "field-error", message));
        if (!first) first = target;
      } else {
        banner.textContent = message;
        banner.hidden = false;
      }
    });
    if (first) first.scrollIntoView({ block: "nearest" });
    return shown;
  }

  // Which step a field belongs to, so a save that fails on something asked
  // three screens ago can go back to it instead of reporting it where it
  // cannot be fixed.
  function stepOf(steps, name) {
    if (name === "__name") return 0;
    return steps.findIndex((step) =>
      [...step.fields, ...step.advanced].some((f) => f.name === name)
    );
  }

  /**
   * @param opts.kind      section kind, for validation
   * @param opts.steps     step descriptions from the schema
   * @param opts.values    current values, or null when creating
   * @param opts.lead      builds the fields above the steps (name, type, …)
   * @param opts.decorate  per-step hook, for the pickers the schema cannot know
   * @param opts.variant   what to validate against
   * @param opts.onSave    receives the collected values
   */
  function openForm(opts) {
    const modal = $("#form-modal");
    const form = $("#section-form");
    const wizard = !opts.values;
    // Null while editing: the name is fixed then, and checking it against the
    // list would flag the section for colliding with itself.
    const existingNames = opts.taken ? opts.taken() : null;
    let at = 0;

    $("#form-modal-title").textContent = opts.title;
    const hint = $("#form-modal-hint");
    hint.textContent = opts.hint || "";
    hint.hidden = !opts.hint;
    $("#form-error").hidden = true;

    const progress = $("#form-progress");
    const lead = el("div", "form-lead");
    const stage = el("div", "form-stage");

    const paint = () => {
      const steps = opts.steps();
      form.replaceChildren();
      lead.replaceChildren();
      // The name and the type are asked once, at the start. Repeating them
      // above every step made the wizard look like it was not progressing.
      if (opts.lead && (!wizard || at === 0)) opts.lead(lead, paint);
      form.appendChild(lead);
      stage.replaceChildren();

      if (wizard) {
        at = Math.min(at, steps.length - 1);
        const step = steps[at];
        progress.hidden = false;
        progress.replaceChildren();
        steps.forEach((s, index) => {
          const dot = el("span", "dot");
          dot.setAttribute("aria-current", String(index === at));
          dot.classList.toggle("done", index < at);
          dot.title = s.title;
          progress.appendChild(dot);
        });
        progress.appendChild(el("span", "progress-text", `Schritt ${at + 1} von ${steps.length}`));
        const head = el("div", "step-head");
        head.appendChild(el("h3", null, step.title));
        if (step.subtitle) head.appendChild(el("p", null, step.subtitle));
        stage.appendChild(head);
        const body = el("div");
        if (opts.decorate) opts.decorate(step, body, paint);
        renderStep(body, step, opts.values, opts.hidden && opts.hidden());
        stage.appendChild(body);
        $("#form-back").hidden = at === 0;
        $("#form-next").hidden = at >= steps.length - 1;
        $("#form-save").hidden = at < steps.length - 1;
      } else {
        progress.hidden = true;
        steps.forEach((step, index) => {
          const panel = el("details", "step");
          if (index === 0) panel.open = true;
          const summary = el("summary");
          summary.appendChild(el("b", null, step.title));
          if (step.subtitle) summary.appendChild(el("span", null, step.subtitle));
          panel.appendChild(summary);
          const body = el("div", "step-body");
          if (opts.decorate) opts.decorate(step, body, paint);
          renderStep(body, step, opts.values, opts.hidden && opts.hidden());
          panel.appendChild(body);
          stage.appendChild(panel);
        });
        $("#form-back").hidden = true;
        $("#form-next").hidden = true;
        $("#form-save").hidden = false;
      }
      form.appendChild(stage);
    };

    paint();
    modal.classList.remove("hidden");

    const close = () => {
      modal.classList.add("hidden");
      progress.hidden = true;
      ["#form-save", "#form-next", "#form-back"].forEach((sel) => {
        $(sel).replaceWith($(sel).cloneNode(true));
      });
    };
    $("#form-modal-close").onclick = close;
    $("#form-cancel").onclick = close;
    $(".modal-backdrop").onclick = close;

    // Carry what is on screen into opts.values before repainting, so stepping
    // back and forth does not quietly discard what was typed.
    const remember = () => {
      opts.values = { ...(opts.values || {}), ...readForm(form) };
    };

    // The name is asked on the first step, so it is checked there — against
    // the same rule the writer applies, which the schema hands over rather
    // than the browser keeping its own copy of it.
    const checkName = () => {
      if (existingNames === null) return null;
      const rule = (state.schema && state.schema.name_rule) || {};
      const value = (opts.name() || "").trim();
      if (!value) return { __name: rule.missing || "Bitte einen Namen angeben." };
      if (rule.pattern && !new RegExp(rule.pattern).test(value)) {
        return { __name: rule.message || "Ungültiger Name." };
      }
      if (existingNames.includes(value)) {
        return { __name: `${value} gibt es schon.` };
      }
      return null;
    };

    // A required field left empty, caught before the server says so. The
    // config validators speak English and are shared with the command line;
    // the obvious case does not need to reach them to be reported here.
    const checkRequired = (step, typed) => {
      const missing = [...step.fields, ...step.advanced].find(
        (f) => f.required && (typed[f.name] === undefined || typed[f.name] === "")
      );
      return missing ? { [missing.name]: `${missing.label} wird gebraucht.` } : null;
    };

    $("#form-next").onclick = async () => {
      const typed = readForm(form);
      // The lead carries the name and the type; on the first step they are on
      // screen and must be able to report, which scoping to the stage alone
      // made impossible.
      // A field belongs to this screen when its step is the one on screen;
      // the name and the type are asked on the first.
      const mine = (name) => !wizard || stepOf(opts.steps(), name) === at;
      const nameError = at === 0 && checkName();
      if (nameError && showErrors(form, nameError, mine)) return;
      const blank = checkRequired(opts.steps()[at], typed);
      if (blank && showErrors(form, blank, mine)) return;
      let errors = {};
      try {
        const res = await api(`/api/sections/${opts.kind}/validate`, {
          method: "POST",
          body: { name: opts.name() || "probe", variant: opts.variant(), values: typed },
        });
        errors = res.errors || {};
      } catch (err) {
        errors = { "": err.message };
      }
      if (showErrors(form, errors, mine)) return;
      const localError = opts.checkStep && opts.checkStep(opts.steps()[at], typed);
      if (localError && showErrors(form, localError, mine)) return;
      remember();
      at += 1;
      paint();
    };

    $("#form-back").onclick = () => {
      remember();
      at -= 1;
      paint();
    };

    $("#form-save").onclick = async () => {
      showErrors(form, {});
      remember();
      const nameError = checkName();
      if (nameError) {
        if (wizard) at = 0;
        paint();
        showErrors(form, nameError, null);
        return;
      }
      try {
        const errors = await opts.onSave(opts.values || readForm(form));
        if (errors && Object.keys(errors).length) {
          // In a wizard the offending field may be three screens back, where
          // an error message is no use at all. Go to it.
          if (wizard) {
            const steps = opts.steps();
            const targets = Object.keys(errors)
              .map((name) => stepOf(steps, name))
              .filter((index) => index >= 0);
            if (targets.length) {
              at = Math.min(...targets);
              paint();
            }
          }
          showErrors(form, errors, null);
          return;
        }
        close();
        await refresh();
        showView(document.querySelector("#nav button[aria-current='true']").dataset.view);
      } catch (err) {
        const banner = $("#form-error");
        banner.textContent = err.message;
        banner.hidden = false;
      }
    };
  }

  function nameField(host, id, label, placeholder) {
    const wrap = el("div", "field");
    wrap.dataset.field = "__name";
    wrap.appendChild(el("label", null, label));
    const input = el("input");
    input.id = id;
    if (placeholder) input.placeholder = placeholder;
    wrap.appendChild(input);
    host.appendChild(wrap);
    return input;
  }

  /* ---------- Jagden ------------------------------------------------------ */

  function huntEditor(existing) {
    const marketplaces = of("marketplace").map((m) => m.name);
    let chosen = existing
      ? Array.isArray(existing.values.marketplace)
        ? existing.values.marketplace.slice()
        : [existing.values.marketplace || marketplaces[0]].filter(Boolean)
      : marketplaces.slice(0, 1);
    let name = existing ? existing.name : "";

    openForm({
      kind: "item",
      title: existing ? `Jagd ${existing.name}` : "Neue Jagd",
      hint: existing
        ? ""
        : "Eine Jagd beschreibt, wonach gesucht wird. Sie kann mehrere Marktplätze gleichzeitig beobachten.",
      values: existing ? { ...existing.values } : null,
      steps: () => stepsFor("item", chosen),
      variant: () => (chosen.length === 1 ? chosen[0] : chosen),
      name: () => name,
      taken: existing ? null : () => of("item").map((s) => s.name),
      lead: (host) => {
        if (existing) return;
        const input = nameField(host, "hunt-name", "Name der Jagd", "gopro");
        input.value = name;
        input.addEventListener("input", () => {
          name = input.value.trim();
        });
      },
      // The marketplace picker belongs at the top of "Wo gesucht wird", but no
      // config field describes it — `marketplace` is the discriminator.
      decorate: (step, host, repaint) => {
        if (step.id !== "where") return;
        const wrap = el("div", "field");
        wrap.appendChild(el("span", "field-label", "Marktplätze"));
        const picker = el("div", "chips");
        marketplaces.forEach((market) => {
          const chip = el("button", "chip-pick");
          chip.type = "button";
          chip.textContent = market;
          chip.setAttribute("aria-pressed", String(chosen.includes(market)));
          chip.addEventListener("click", () => {
            chosen = chosen.includes(market)
              ? chosen.filter((m) => m !== market)
              : chosen.concat(market);
            repaint();
          });
          picker.appendChild(chip);
        });
        wrap.appendChild(picker);
        wrap.appendChild(
          el(
            "p",
            "default-note",
            chosen.length > 1
              ? "Angeboten werden nur Optionen, die alle gewählten Marktplätze kennen."
              : "Leer lassen geht nicht — mindestens einer muss gewählt sein."
          )
        );
        if (!marketplaces.length) {
          wrap.appendChild(
            el("p", "field-error", "Noch kein Marktplatz eingerichtet — unter Verbindungen anlegen.")
          );
        }
        host.appendChild(wrap);
        const inherited = chosen
          .map((market) => of("marketplace").find((s) => s.name === market))
          .filter((section) => section && section.values.search_city);
        if (inherited.length) {
          host.appendChild(
            el(
              "p",
              "default-note",
              `Leer lassen heisst: es gilt, was beim Marktplatz steht (${inherited
                .map((s) => [].concat(s.values.search_city).join(", "))
                .join(" · ")}).`
            )
          );
        }
      },
      checkStep: (step) => {
        if (step.id === "where" && !chosen.length)
          return { "": "Mindestens ein Marktplatz muss gewählt sein." };
        return null;
      },
      onSave: async (values) => {
        if (!name) return { __name: "Bitte einen Namen angeben." };
        if (!chosen.length) return { "": "Mindestens ein Marktplatz muss gewählt sein." };
        const payload = {
          name,
          variant: chosen.length === 1 ? chosen[0] : chosen,
          values,
        };
        const res = existing
          ? await api(`/api/sections/item/${encodeURIComponent(name)}`, {
              method: "PUT",
              body: payload,
            })
          : await api("/api/sections/item", { method: "POST", body: payload });
        return res.errors;
      },
    });
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

  // Sections that exist once and carry no name of their own, as [monitor] does.
  const SINGLETON_KINDS = new Set(["monitor"]);

  function sectionEditor(kind, existing, title, hint, preset) {
    const variants = Object.keys((state.schema.kinds && state.schema.kinds[kind]) || {});
    let variant = existing ? existing.variant || variants[0] : preset || variants[0];
    let name = existing
      ? existing.name
      : SINGLETON_KINDS.has(kind)
      ? kind
      : variants.length > 1
      ? variant
      : "";
    let touched = false;
    // Which channels a recipient uses. Derived on open from what is filled in,
    // so an existing recipient shows the ways it already has.
    const catalogue = (state.schema && state.schema.channels) || [];
    let picked = catalogue
      .filter((c) => existing && c.required.every((f) => existing.values[f]))
      .map((c) => c.id);
    if (!existing && catalogue.length) picked = [];

    const channelFields = () => {
      const wanted = new Set();
      catalogue
        .filter((c) => picked.includes(c.id))
        .forEach((c) => [...c.required, ...c.optional].forEach((f) => wanted.add(f)));
      return wanted;
    };

    openForm({
      kind,
      title,
      hint,
      values: existing ? { ...existing.values } : null,
      steps: () => stepsFor(kind, variant),
      variant: () => variant,
      name: () => name,
      taken: existing || SINGLETON_KINDS.has(kind) ? null : () => of(kind).map((s) => s.name),
      // Everything a channel owns that is not switched on stays out of the DOM,
      // so an unpicked way cannot be half-filled and then silently ignored.
      hidden: () => {
        if (kind !== "user") return null;
        const shown = channelFields();
        const all = new Set();
        catalogue.forEach((c) => [...c.required, ...c.optional].forEach((f) => all.add(f)));
        return new Set([...all].filter((f) => !shown.has(f)));
      },
      lead: (host, repaint) => {
        if (variants.length > 1 && !existing) {
          const wrap = el("div", "field");
          wrap.appendChild(el("label", null, "Typ"));
          const select = el("select");
          variants.forEach((v) => {
            const option = el("option", null, v);
            option.value = v;
            select.appendChild(option);
          });
          select.value = variant;
          wrap.appendChild(select);
          host.appendChild(wrap);
          select.addEventListener("change", () => {
            variant = select.value;
            if (!touched) name = variant;
            // The type decides which steps exist at all — tutti opens on its
            // search area, facebook on a login — so the whole form is redrawn.
            repaint();
          });
        }
        // A singleton section is called after its kind; there is nothing to name.
        if (existing || SINGLETON_KINDS.has(kind)) return;
        const input = nameField(
          host,
          "section-name-new",
          "Name",
          kind === "user" ? "ich" : ""
        );
        input.value = name;
        input.addEventListener("input", () => {
          touched = true;
          name = input.value.trim();
        });
      },
      decorate: (step, host, repaint) => {
        if (step.id !== "channels") return;
        const wrap = el("div", "field");
        const picker = el("div", "chips");
        catalogue.forEach((channel) => {
          const chip = el("button", "chip-pick");
          chip.type = "button";
          chip.textContent = channel.label;
          chip.setAttribute("aria-pressed", String(picked.includes(channel.id)));
          chip.addEventListener("click", () => {
            picked = picked.includes(channel.id)
              ? picked.filter((c) => c !== channel.id)
              : picked.concat(channel.id);
            repaint();
          });
          picker.appendChild(chip);
        });
        wrap.appendChild(picker);
        wrap.appendChild(
          el(
            "p",
            "default-note",
            picked.length
              ? `Im nächsten Schritt werden nur die Felder dieser ${
                  picked.length === 1 ? "Auswahl" : "Auswahlen"
                } abgefragt.`
              : "Ohne Weg bekommt dieser Empfänger keine Benachrichtigungen."
          )
        );
        host.appendChild(wrap);
      },
      checkStep: (step) => {
        if (step.id === "channels" && !picked.length)
          return { "": "Bitte mindestens einen Weg wählen." };
        return null;
      },
      onSave: async (values) => {
        if (!name) return { __name: "Bitte einen Namen angeben." };
        const payload = { name, variant, values };
        const res = existing
          ? await api(`/api/sections/${kind}/${encodeURIComponent(name)}`, {
              method: "PUT",
              body: payload,
            })
          : await api(`/api/sections/${kind}`, { method: "POST", body: payload });
        return res.errors;
      },
    });
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

    // Without this there is no way to add tutti at all: a fresh install only
    // writes a facebook section, so the list would stay one row forever.
    const addMarket = el("div", "row");
    const missing = (state.schema.marketplaces || []).filter(
      (name) => !of("marketplace").some((s) => (s.variant || s.name) === name)
    );
    addMarket.appendChild(
      Object.assign(el("div", "who"), {
        innerHTML: missing.length
          ? `<b>Noch nicht eingerichtet</b><span>${missing.join(", ")}</span>`
          : "<b>Weiterer Marktplatz</b><span>Auch mehrere Konten desselben Anbieters sind möglich.</span>",
      })
    );
    const addBtn = el("button", "primary small", "Marktplatz hinzufügen");
    addBtn.addEventListener("click", () =>
      sectionEditor(
        "marketplace",
        null,
        "Neuer Marktplatz",
        "Der Typ bestimmt, welche Optionen es gibt. Der Name darf frei sein.",
        missing[0]
      )
    );
    addMarket.appendChild(addBtn);
    markets.appendChild(addMarket);

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
    const addUser = el("div", "row");
    addUser.appendChild(
      Object.assign(el("div", "who"), {
        innerHTML: of("user").length
          ? "<b>Weiterer Empfänger</b><span>Jeder Empfänger kann eigene Wege haben.</span>"
          : "<b>Kein Empfänger</b><span>Ohne Benutzer gibt es keine Benachrichtigungen.</span>",
      })
    );
    const addUserBtn = el("button", of("user").length ? "ghost small" : "primary small", "Empfänger hinzufügen");
    addUserBtn.addEventListener("click", () => sectionEditor("user", null, "Neuer Empfänger", ""));
    addUser.appendChild(addUserBtn);
    users.appendChild(addUser);

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
      // Passing a made-up section when none exists made the form send a PUT,
      // which failed with "Abschnitt monitor.monitor existiert nicht".
      sectionEditor(
        "monitor",
        state.sections.find((s) => s.kind === "monitor") || null,
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
      // app.js skips painting a shut drawer, so ask it to catch up on open.
      if (open && typeof window.aimmRenderLogs === "function") window.aimmRenderLogs();
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
