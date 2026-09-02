# Self-service Web UI — Design Spec

Visual and interaction direction for the UI overhaul. The implementation plan
lives in `docs/superpowers/plans/2026-09-02-ui-selfservice.md`.

---

## Intent

**The human.** Someone hunting one specific second-hand thing — a road bike, a
GoPro, a lens — who does not want to refresh tutti.ch five times a day for three
weeks. They are not an operator watching a dashboard. They set this up once, then
mostly *receive*. They open the UI at breakfast or after a push notification
lands, on a phone as often as a laptop.

**The task.** Two verbs, and they are unequal:

1. *Set it up* — once, and it must not require a shell. This is where the whole
   "everything in the UI" requirement lives.
2. *Judge a find* — repeatedly. Is CHF 265 for this GoPro a good price or not?

Everything else (logs, intervals, cache) is maintenance and must recede.

**The feel.** A patient instrument that works the night shift. Quiet while
nothing is happening; precise and confident the moment it has something. Not
cheerful, not corporate, not a "platform". Closer to a market inspector's desk
than to a SaaS console.

---

## Domain exploration

**Domain** — the world of second-hand hunting: the stakeout · the find · the
going rate · the small-ads column · the Brockenhaus sorting table · condition
grading (Neu / Gebraucht / Defekt) · the price tag on a string · the canton as
territory · the night watch, since listings appear at 23:40 and are gone by 08:00.

**Color world** — the ground is a cool, blue-biased charcoal: a night-shift
instrument, and a nod to the marketplaces it watches. Against that cool ground
sits one warm accent, brass from a price tag and a loupe (`#D9A441`) — warm on
cool is what keeps it from reading as another grey admin panel. Green
(`#5FA97A`) and red (`#D4655A`) are spent only on the price verdict.

Rejected: a warm brown/newsprint ground. It suited the flea-market metaphor but
read as dusty rather than as an instrument, and the client asked for grey.

**Signature — the price ruler.** A horizontal scale under every find showing the
distribution of comparable offers (min · median · max) with this listing's price
marked on it, tinted green when it sits left of the median and red when right.
It is the product's whole value proposition rendered as one glanceable object,
it is fed by `price_stats.py` which no other tool has, and it repeats dozens of
times per screen. Nothing generic looks like it.

The reference is **global per hunt**, pooled across every marketplace that hunt
watches, so the median describes the item rather than the site. The readout names
the composition — "30 Angebote (21 tutti, 9 Facebook)" — because pooling would
otherwise quietly hide that the two sites price differently. Prices are converted
to the judged listing's currency; anything unconvertible is dropped rather than
mixed in.

**Defaults being rejected**

| Default | Instead |
|---|---|
| A KPI row — "Found 47 · Searches 12 · Avg rating 3.8" in three identical boxes | Vanity metrics for a product nobody stares at. The top of the screen is the newest **finds**, because that is why the page was opened. |
| Raw TOML editor as *the* config surface | Task-shaped forms ("Neue Jagd"), with the TOML editor demoted to an expert escape hatch. |
| `--accent: #5b8cff` + `system-ui` (today's tokens) | A palette and two typefaces taken from the domain. Read today's variables aloud — `--bg`, `--text-dim`, `--accent` — and they could belong to any project. That is the token test failing. |
| Sidebar nav + card grid + tabs | The left rail is not navigation, it is the **list of your hunts** with live state. Selecting one filters the feed. Navigation that *is* the product. |

---

## Tokens

Dark-first: this thing runs at night and its notifications arrive at night. Light
mode inverts lightness only, keeping the same hue.

```
/* surfaces — cool charcoal, blue bias. One hue, lightness only. */
--slate-000  #121418   canvas
--slate-100  #191C22   raised (cards, rail)
--slate-200  #212530   overlay (dropdown, dialog)
--slate-300  #2B303C   input wells — darker than surroundings, they receive content

/* ink */
--ink-000    #E6E9EF   primary
--ink-100    #AAB1BF   secondary
--ink-200    #7C8494   tertiary
--ink-300    #565D6B   muted / disabled

/* edges — low-opacity, never solid hex */
--edge       rgba(230,233,239,0.09)
--edge-soft  rgba(230,233,239,0.05)
--edge-loud  rgba(230,233,239,0.16)

/* the one accent: brass, warm against the cool ground. */
--brass      #D9A441
--brass-dim  #8C6A26

/* semantic — colour genuinely means something here:
   this is the verdict on a price, not decoration. */
--under      #5FA97A   below market
--over       #D4655A   above market
```

Marketplaces are **not** colour-coded by their brand hues. A tutti red would
collide with `--over` at a glance, and per-marketplace accents would dilute the
single accent. Source is a plain tinted label instead, which also means a fourth
marketplace costs no new colour.

Four text levels, one accent, semantics reserved for the price verdict. Roughly
60 % paper / 30 % ink and structure / 10 % brass.

**Type.** Sans only — the client rejected a serif. Two families, both OFL and
**self-hosted**: the container must render identically with no internet, and a
paid product should not leak viewers to a font CDN.

- **Archivo** (600/700) — prices, find titles, the wordmark. A sturdy news
  grotesque: tight, slightly compressed, with tabular figures so a refreshing
  price never shifts the layout. Set with −0.028em tracking at display sizes.
- **IBM Plex Sans** (400/500) — every control, label and body line. Engineered
  rather than neutral, and deliberately not Inter. **IBM Plex Mono** for the
  expert TOML editor, so the escape hatch stays in the family.

Separation comes from weight and tracking as much as family: Archivo 700 at 30px
against Plex 400 at 13px is unmistakable even though both are grotesques.

Scale: 15px base, ratio 1.25 → `12 · 15 · 19 · 24 · 30 · 38`. Hierarchy comes
from three levers together, never size alone — the find price is
`30 / 600 / ink-000 / tabular`, its label `12 / 500 / ink-200`.

**Spacing.** 4px base. Density varies by zone on purpose: the hunts rail is tight
(12px) because it is a control surface; the finds feed is airy (20px card
padding, 16px gaps) because it is a reading surface. Same number everywhere is
the sound of nobody deciding.

**Depth.** Borders and surface-tint shifts only — no drop shadows. It suits a
dense, technical instrument, and shadows barely read on dark anyway. One strategy,
committed to.

**Radius.** 4 inputs · 8 cards · 12 dialogs. Concentric: nested radius = outer − padding.

---

## Layout

```
┌────────────────────────────────────────────────────────────────────────┐
│ aimm        ● wacht · nächste Suche 14:20        [Browser] [⚙]         │  thin status strip
├──────────────┬─────────────────────────────────────────────────────────┤
│ JAGDEN       │  FUNDE                                                  │
│              │  ┌───────────────────────────────────────────────────┐  │
│ ▸ GoPro Hero │  │ [bild]  GoPro HERO11 Black inkl. 2 Akkus          │  │
│   tutti · 3  │  │         CHF 265.–              ★★★★☆              │  │
│   vor 12 min │  │         8050 Zürich · Gebraucht · vor 20 Min      │  │
│              │  │    ├──────────●─────────┼──────────────┤          │  │  ← signature
│ ▸ Rennvelo   │  │    15        265       310            499         │  │
│   tutti · 0  │  │    38 % unter dem Median von 21 Angeboten         │  │
│   vor 41 min │  └───────────────────────────────────────────────────┘  │
│              │  ┌───────────────────────────────────────────────────┐  │
│ + Neue Jagd  │  │ …                                                 │  │
└──────────────┴─────────────────────────────────────────────────────────┘
  ▸ Protokoll (23)                                       collapsed drawer
```

- **Rail 260px**, content fluid. 260 against fluid says "the hunts serve the
  finds" — they are not peers.
- **Focal point per view.** Finds feed → the newest find's price. Hunt form →
  the search phrase field. Setup → the single next action. Named before building.
- **Logs are demoted** from half the screen to a collapsed drawer. Today's split
  encodes a developer's priorities, not a user's.
- Below 900px the rail collapses to a horizontal hunt selector; the find card
  stacks image over text and the ruler stays full width.

---

## Components worth specifying

**Price ruler** — 4px track, `--paper-300`; min/median/max ticks in `--ink-300`;
the listing's marker a 10px brass dot with a 2px halo of the verdict colour.
Labels `12 / 500 / tabular`. Degrades honestly: with fewer than
`MIN_REFERENCE_PRICES` comparables it renders as "keine Vergleichsbasis" rather
than an invented scale — the backend already returns `""` in that case.

**Find card** — 20px padding, 8px radius, `--paper-100`, 1px `--edge`. Image
96×96 with a 1px inset `rgba(255,255,255,0.1)` outline. Title Newsreader
19/500 `text-wrap: pretty`. Price 30/600 tabular. The AI verdict is a chip, not
a paragraph.

**Hunt row** — 12px vertical padding, name 15/500, marketplace + hit count
12/400 `--ink-200`, "vor 12 min" 12/400 `--ink-300`. Active row: brass 2px left
edge, `--paper-200` fill. No icons — the name is the identity.

**Connection card** (marketplace / AI / notification) — each carries a live
status dot and a **Test** button that reports inline, never in a toast that
disappears before it is read.

**States are not optional.** Every find list needs loading, empty and error.
Empty is an invitation: "Noch keine Funde. Die erste Suche läuft um 14:20." —
not a shrug.

**Motion.** Barely any. This is checked several times a day, so nothing that
repeats gets animated. Dialogs 200ms `cubic-bezier(0.23,1,0.32,1)`, press
`scale(0.97)`, `transform`/`opacity` only, `prefers-reduced-motion` honoured.
A new find arriving does *not* slide in — it is simply there.

---

## Copy

German, sentence case, active voice. The product's vocabulary is the domain's:
**Jagd** (a saved search), **Fund** (a matched listing), **Vergleichsbasis** (the
reference set), **wacht / schläft** for monitor state. An action keeps its name
end to end — the button says "Jagd speichern", the confirmation says
"Jagd gespeichert". Errors say what happened and what to do, never apologise.

German it is, confirmed. Strings live in one dictionary
(`webui/static/strings.de.js`) so a second locale later is a data change, not a
refactor. Code, comments and identifiers stay English.
