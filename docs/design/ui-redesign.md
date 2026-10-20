# UI/UX redesign — "Futures map"

Status: design spec for the web client redesign (also the reference for the native Expo app).
Owner: product/UX. Scope: `src/udse/web/static/*` plus two read-only API endpoints.

---

## 1. Why the current UI fails

The first client exposed the engine faithfully, but as an engine console:

| Problem | Symptom in v1 UI | Consequence |
|---|---|---|
| Futures shown as a list | Trajectory "cards" stacked vertically, each a paragraph of metrics | Nobody can see *that* paths diverge, *where*, or *why*. The core idea of the product — branching futures from one present — is invisible. |
| Time is hidden | Timeline only exists inside one run; the decision page has no time axis | The horizon (5 hours vs 2 years) does not shape the experience. |
| Evidence is a table | Claims table with status chips | The difference between "what we actually know" and "what we simulated" — the product's integrity promise — reads as bookkeeping. |
| Model is a list of mechanisms | Long cards with operator names | Users cannot tell which outcomes rest on solid ground and which on assumptions. |
| Coverage is a grouped list | 20 frontier items under status headings | Unexplored and ruled-out possibilities feel like errata, not part of the map. |
| Engine vocabulary everywhere | `TERMINAL_STATE`, `B_ASSUMPTION_BOUND`, `company.cash_usd` | Readers need the spec open to understand the screen. |
| No visual identity | Generic cards, system font | Nothing signals "this is a place to explore possible futures". |

## 2. Design goals

1. **Show the shape of the future at a glance.** One picture should answer: how many distinct futures were explored, where they split, how they end, and what was left unexplored.
2. **Make the epistemic boundary physical.** Real evidence lives on one side of a visible *Present line*; simulated futures live on the other. They never share a visual language.
3. **Explain, don't dump.** Every path is a story of moments with causes. Engine detail is one tap away, never the default.
4. **Honest by construction.** No odds, no averages, no "best plan". Run counts are coverage, not likelihood. Weak assumptions look weak (the thread frays).
5. **Same grammar for every decision.** A 5-hour dinner and a 2-year fundraise use the same map, only the time scale changes.
6. **Beautiful, calm, legible.** Dark-first "observatory" aesthetic with an equally considered light "archive" theme; works one-handed on a phone.

Non-goals: dashboards of KPIs, recommendation scores, gamified engagement, notifications.

## 3. Core metaphor: the Futures map (branching timeline)

Inspired by branching-timeline imagery (a single sacred line that splits into variants at nexus points), but grounded in what the engine actually computes:

```
 KNOWN NOW │                SIMULATED FUTURES  →  time
           │
  ✓ 18     │      ╭──────●────────────── ◼ Ran out of cash  · Jan 2028
  ⌛  1     │     ╱  cut burn  D part-time
  ?  3     │    ╱
  ⚠  1     ●═══●════●══════●═════════════════════════════○ Funded · horizon
           │ nexus  prototype  term sheet  accepted
  gaps:    │    ╲
  R-C table│     ╲──────────────────────────────────────○ Funded (pilot first)
  unknown  │      ┄┄┄┄┄ Bootstrapped expansion — needs a revenue model
           │      ┄┄✂  Late dinner after closing — ruled out (official hours)
           │
           NOW (simulation starts · evidence as of 18 Oct 09:00)
```

### 3.1 What a thread is
A **thread** is one distinct simulated trajectory. Runs whose committed event sequences are identical (for example a preview and a detailed rerun of the same deterministic arm) collapse into one thread that lists all its runs.

### 3.2 Where threads split (nexus points)
Threads branch at the **first observable difference** in their committed history: the first event whose time, type, actor or world changes differ (the same rule the divergence API uses). Each thread's parent is the already-placed thread with which it shares the longest history. Consequences:
- Policy arms usually split at the very start (the policy is applied at t0).
- Scenario arms may share history for a while and split only when the assumption first matters (e.g. "Climate tightens Oct 2027" splits in Oct 2027). The split label says what differed; the thread's tag says which assumption it carries from the start.
- Forks split exactly at their checkpoint.

A **nexus node** is drawn at each split, labelled on tap with *what happened differently* on each side.

### 3.3 How threads end
| End | Glyph | Meaning |
|---|---|---|
| Reached the horizon | open ring `○` on the horizon edge | Simulated to the end of the chosen horizon |
| Terminal state | filled square `◼` + plain-language outcome | The world reached a modelled end (dined, ran out of cash, no agreement) |
| Stopped early (budget / blocked) | thread fades to dots, grey zone after it | **Not simulated** after this point — never extrapolated |

### 3.4 What else lives on the map
- **Ghost branches** — dotted stubs sprouting near the present for possibilities that were *identified but not simulated*, labelled with why ("needs a delivery model", "set aside by budget"). At most 5 shown; "+N more" opens Possibilities.
- **Pruned branches** — a short stub ending in a cut mark `✂` for families *ruled out by an evidenced constraint*, with the citation on tap.
- **Unreached outcomes** — outcome families no run reached ("Dinner at R-B") appear in Possibilities, not on the map (they have no time position).
- **The fog** — a soft gradient past the outermost lanes reminding that coverage is not exhaustive.

### 3.5 Two lenses on the same map
- **Structure lens (default)** — Y is layout only: a calm tree, trunk centred, branches fanning out. Best for "what can happen".
- **Metric lens** — Y becomes a chosen metric (Cash runway, Customer signals, Spend…). Threads become step lines in value space; shared history overlaps naturally and visibly peels apart at nexus points. Best for "how different are the paths".

Switching lens animates threads between the two layouts (respecting reduced motion).

### 3.6 Fraying: assumptions made visible
Each segment of a thread is drawn with the **weakest evidence tier reached so far** along that thread:

| Tier | Stroke | Plain-language label |
|---|---|---|
| A — grounded | solid, full opacity | "Grounded in evidence" |
| B — assumption-bound | solid, slightly translucent | "Rests on stated assumptions" |
| C — illustrative | long dashes ("fraying") | "Illustrative assumptions" |
| X — limited | sparse dots | "Not modelled responsibly" |

A legend explains this once; it is the only tier encoding on the map.

## 4. Information architecture

```
Worlds (home)
 └─ Decision ("world")
     ├─ Futures      ← default: Futures map + thread story / compare
     ├─ Evidence     ← "What we know now": facts, gaps, freshness, rejected sources
     ├─ World model  ← "How this world works": inputs → processes → outcomes, missing pieces
     ├─ Possibilities← every identified family by status, incl. ruled out & set aside
     └─ Setup        ← question, options, facts, limits, horizon
 Your Agent (profile) ← how your Agent represents you, suggested updates, history
 Integrity            ← the boundary log: every refused crossing between layers
```

Navigation: bottom tab bar on mobile (Worlds · Your Agent · Integrity), top tabs inside a decision. Deep links: `#/w/{id}/futures?thread=…`, `#/w/{id}/evidence`, `#/w/{id}/model`, `#/w/{id}/possibilities`, `#/compare/{a}/{b}`.

## 5. Screen specifications

### 5.1 Worlds (home)
- Each decision is a **world card**: question in display serif; horizon as `18 Oct 2026 → 18 Oct 2028`; a **mini futures map** thumbnail (threads + end glyphs, no labels); three counts: *futures explored*, *possibilities not yet simulated*, *evidence gaps*.
- Primary action: **"Ask a new question"** (sheet with plain fields; advanced facts in a disclosure).
- Empty state explains the product in one sentence and offers the three demo worlds.

### 5.2 Decision header (all tabs)
- Question (display serif, 26–32px), horizon chip, participants as avatars, **world model chip** `World model v2 · built on evidence as of 24 Oct 16:30` (tap → World model).
- Owner actions as quiet buttons: *Rebuild model*, *Explore futures* (runs experiments). Job progress shows inline as a stepped status ("Researching → Building model → Checking → Simulating 9 previews → 3 detailed").
- High-stakes decisions show a persistent ribbon: "High-stakes: numbers from unverified mechanisms are withheld."

### 5.3 Futures (main screen)
Layout — desktop ≥ 1000px: map full width (min 960×460) with the **story panel** to the right (380px) when a thread is selected. Mobile: map vertical (time flows downward), story panel as a bottom sheet.

Map anatomy:
1. **Known-now gutter** (left of the Present line; top block on mobile): evidence counters (✓ verified, ⌛ stale, ? unknown, ⚠ contested/leads), the top 2 *gaps that matter*, and any source-safety warning. Tap → Evidence.
2. **Present line**: amber, softly glowing; caption "Simulation starts · evidence as of …".
3. **Time axis**: ticks adapted to horizon (minutes/hours for an evening, months/quarters for years); the horizon edge is a dashed vertical line.
4. **Threads** with tier-styled strokes, coloured by **outcome family** (same family → same colour across all views).
5. **Moments** (beads) on threads — only key events: people's actions (ringed in the person's colour), thresholds crossed, external shocks, information arriving, constraints blocking, ends. Shared moments are drawn once on the shared segment.
6. **Nexus nodes** at splits.
7. **Ghost and pruned stubs** near the present.
8. **Time cursor**: draggable line (keyboard ←/→ when the map is focused). A readout card follows it: per visible thread, the value of the active metric (metric lens) or the thread's status at that moment.
9. **Thread keys**: numbered end labels ①②③ with a legend list below the map that doubles as the accessible list of threads (outcome, detail level, carried assumptions).

Controls row (above map): Lens `Structure | <metric>` segmented control; toggles *Show previews*, *Show sensitivity probes* (off), *Show unexplored* (on); *Compare* button.

Interactions:
- **Tap thread / key** → focus: other threads dim to 20%, the thread's moments get labels, story panel opens.
- **Tap nexus** → "Paths split here" card: shared history length, first difference on each side, *Compare these two*.
- **Tap moment** → moment card: plain-language headline, who, cause chain ("Why?"), state changes in human terms, evidence/assumption badges, "Simulated — not a real event" stamp, *Try to treat as evidence* (shows the refusal).
- **Tap ghost** → why it was not simulated and what would be needed.
- **Long-press thread (mobile) / shift-click (desktop)** → add to comparison.

### 5.4 Thread story panel
1. Header: colour swatch, thread name (policy + assumptions in words), outcome badge, detail level ("Detailed simulation" / "Exploratory preview"), `simulated` stamp.
2. **One-line synopsis** in plain words ("Ran out of cash in Jan 2028 after the deferred raise started too late").
3. **Moments rail** — vertical timeline of key moments grouped into chapters by gaps in time; each moment: time, headline, actor avatar, icon by kind; expandable *Why?* chain built only from committed events.
4. **State at cursor** — 3–5 key metrics as compact value + sparkline (committed points only).
5. **This path rests on** — carried assumptions (scenario, overlays, illustrative mechanisms) with tier labels in words.
6. **Your reaction** — "This path matters to me" (explicit selection) and "My Agent wouldn't do that…" (correction). Copy states plainly that only explicit reactions count as evidence about you.

### 5.5 Compare
Entering compare with two threads: the map shows only those two plus their shared history (highlighted), the nexus pulses once, and a **divergence card** shows: shared history (N moments, until time), the first difference on each side, what was different by design (policy / assumption / disclosure / overlay), and end-state metrics as paired bars. Banner when models differ: "Model assumptions changed; these are not paired comparisons."

### 5.6 Evidence — "What we know now"
- **Gaps that matter** first: unresolved research needs with consequence ("R-C table for four: unknown — the model cannot seat you there without confirmation").
- **Source safety**: injection warnings ("A review contained instructions; treated as data only").
- **Entity cards** (one per subject: R-A, company, 2025 seed market…): each fact as a row with a status glyph (✓ verified, ⌛ stale, ? unknown, ⚔ conflicted, ☁ lead/not certifiable), value with units, source host, and a **freshness bar**: observed → expires relative to now (expired bars are hatched).
- **Rejected at the gate** collapsed section.
- Visual language: warm "paper/brass" tones and solid shapes — distinct from the luminous simulated palette.

### 5.7 World model — "How this world works"
- **Three-column flow**: *What we start from* (entities with fact counts by status, parameters) → *How things change* (processes: mechanisms and people's choices) → *What we look at* (metrics). Edges show dependencies; tapping a metric highlights its full dependency path and names the **weakest link**.
- **Missing pieces** appear as dashed "?" nodes connected to the metrics they block.
- **Evidence support per outcome**: a 4-step meter per metric with words ("Illustrative — weakest link: investor process (assumed)"). Never a percentage, never averaged.
- Version strip: v1 → v2 with the revision reason and "what changed" (added/removed processes, changed facts).

### 5.8 Possibilities
Inventory grouped by status, in user words, matching map glyphs: *Simulated in detail*, *Previewed*, *Reopened after new evidence*, *Identified, not simulated*, *Set aside by budget*, *Ruled out by an evidenced rule*. Permanent note: "This list is not exhaustive. Possibilities may exist that nobody has identified." Plus *suspected blind spots*.

### 5.9 Your Agent (profile)
- Header: "How your Agent represents you" + privacy note.
- **Preferences** as diverging bars (dislikes ← 0 → likes) with unit-aware labels; **hard boundaries** as distinct cards with a lock glyph and the user's own words; **unknowns** dashed with "We don't know yet".
- Status in words (Confirmed by you / Inferred / Unknown) and an evidence count with links.
- **Suggested updates** inbox (approve / reject); shadow-mode items labelled "observed for calibration, never applied".
- Version history rail with rollback.

### 5.10 Integrity
A four-zone diagram — *Real evidence* · *World model* · *Simulated futures* · *Your profile* — with arrows showing allowed flows and red ✕ counts for refused crossings; below, the refusal log in plain language.

## 6. Language guide
| Engine term | UI term |
|---|---|
| run / trajectory | path / future |
| screening run | exploratory preview |
| depth run | detailed simulation |
| TERMINAL_STATE + outcome | the outcome in words ("Ran out of cash") |
| COMPLETED_HORIZON | "Reached the horizon" |
| BUDGET_EXHAUSTED | "Stopped early — not simulated after this" |
| A/B/C/X tiers | Grounded / Rests on stated assumptions / Illustrative / Not modelled |
| frontier item | possibility |
| state ref `company.cash_usd` | the state variable's label ("Cash") |
| Agent | "Person A's Agent" (never "Person A did") |
| arm label `Raise now × EVIDENCE SCENARIO: …` | policy name + condition: "Raise now — if the climate tightens"; the scenario kind stays on a chip ("Evidence scenario: …") |
| policy rationale `Object proposal p2 (r_c): cannot verify hard constraint(s) […] Factors: …` | "Objects to R-C — can't confirm gluten safe. Weighed: cost per person $22" — the exact trace stays one click away ("Exact trace") and is never rewritten in the event log |
| metric `budget_runway_months` | "Budget runway" (the unit lives on the axis) |

Outcome codes are humanised (`CASH_EXHAUSTED` → "Ran out of cash", `NO_AGREEMENT_BY_DEADLINE` → "No agreement by the decide-by time").

## 7. Visual system

### 7.1 Themes (CSS tokens; dark default via `prefers-color-scheme`, manual toggle)
| Token | Dark ("observatory") | Light ("archive") |
|---|---|---|
| `--bg` | `#0a0c11` | `#f3eee4` |
| `--surface` | `#11141b` | `#fbf8f1` |
| `--surface-2` | `#181c25` | `#efe8db` |
| `--line` | `#262b36` | `#dcd2c0` |
| `--text` | `#ece6d9` | `#1d1913` |
| `--muted` | `#958e80` | `#6c6355` |
| `--present` (amber) | `#f5a524` + glow | `#b5650d` |
| `--ground` (evidence brass) | `#cdb68b` | `#7a6230` |
| `--ghost` | `#5b6170` | `#a39a8a` |
| `--cut` | `#e0675f` | `#b23a32` |

Outcome palette (8 hues, luminous in dark, inked in light; same order everywhere):
dark `#5eead4 #a78bfa #fb7185 #fbbf24 #60a5fa #a3e635 #f472b6 #38bdf8` ·
light `#0f766e #6d28d9 #be123c #a16207 #1d4ed8 #4d7c0f #be185d #0369a1`.
People (Agents) use a separate desaturated palette for avatar rings.

### 7.2 Typography
- Display: **Fraunces** (variable serif) — questions, outcome headlines, big numbers.
- UI: **Inter** — controls, body.
- Data: **JetBrains Mono** — times, values, hashes.
- Fallbacks to system stacks; the UI must remain fully usable offline without web fonts.

### 7.3 Texture & motion
- Threads: 2.5px (detailed) / 1.5px (preview); subtle outer glow in dark theme only.
- Present line: 2px amber with a soft pulse (disabled under reduced motion).
- Load: threads draw in left→right over 700ms; lens switch tweens 400ms; nexus pulse once on focus.
- No parallax, no auto-playing animation loops beyond the present-line glow.

## 8. Responsive behaviour
| Width | Map orientation | Story panel | Notes |
|---|---|---|---|
| ≥ 1000px | horizontal (time →) | right side panel | known-now gutter left |
| 640–999px | horizontal | bottom sheet | gutter collapses to a strip above map |
| < 640px | **vertical (time ↓)** | bottom sheet | lanes ≥ 26px; previews hidden by default; keys + legend list |

16px side gutters; no horizontal page scroll; the map itself never requires horizontal scrolling.

## 9. Accessibility
- The map is an `svg` with `role="img"` and a generated text summary; the **thread legend list** is the full accessible equivalent (each item a button).
- Keyboard: Tab through threads/keys; Enter focuses; ←/→ moves the time cursor; Esc clears focus.
- Never encode meaning by colour alone: outcome glyphs, tier stroke patterns, text labels.
- Contrast ≥ 4.5:1 for text in both themes; focus rings visible.
- `prefers-reduced-motion` disables draw-in, tweens and glow pulse.

## 10. Data contracts (new, read-only)

`GET /v1/decisions/{id}/futures` →
```json
{
  "horizon": {"start": "...", "end": "...", "resolution": "minute|day|month"},
  "evidence_as_of": "...",
  "model": {"version": 1, "hash": "...", "high_stakes": false},
  "metrics": [{"id": "cash_runway_months", "label": "Cash runway", "unit": "month", "numeric": true}],
  "people": [{"id": "founder_a", "name": "Founder A (CEO)"}],
  "families": [{"key": "CASH_EXHAUSTED", "label": "Ran out of cash", "color_index": 0}],
  "threads": [{
    "thread_id": "t1", "run_ids": ["run_…"], "rep_run_id": "run_…", "label": "Raise in 12 months",
    "detail": "detailed|preview|probe", "family": "CASH_EXHAUSTED", "outcome_label": "Ran out of cash",
    "status": "TERMINAL_STATE", "ended_at": "...", "horizon_reached": false, "stopped_early": false,
    "parent": "t0", "split_at": "...", "split_index": 12, "shared_events": 12,
    "first_difference": {"self": "Founder A's Agent: cut burn", "parent": "Monthly outflow …"},
    "carries": ["Scenario: Tighter climate"], "synopsis": "...",
    "tier_path": [["2026-10-18T…", "B_ASSUMPTION_BOUND"], ["…", "C_ILLUSTRATIVE"]],
    "series": {"cash_runway_months": [["…", 12.0], ["…", 11.0]]},
    "moments": [{"seq": 5, "t": "...", "kind": "action|threshold|external|info|blocked|start|end|agreement",
                 "headline": "Founder A's Agent cut monthly costs", "actor": "founder_a", "tier": "B_ASSUMPTION_BOUND"}]
  }],
  "ghosts": [{"id": "fam_…", "label": "Takeout at home", "status": "DISCOVERED_UNSIMULATED", "why": "needs a delivery model", "kind": "unsimulated|ruled_out|set_aside|reopened", "citation": []}],
  "ghosts_total": 14,
  "evidence": {"verified": 18, "stale": 1, "unknown": 3, "contested": 1, "warnings": ["…"], "gaps": [{"question": "…", "consequence": "…"}]}
}
```

`GET /v1/decisions/{id}/world-model?version=` →
```json
{"inputs": [{"id": "entity:r_a", "label": "R-A (fixture)", "kind": "entity", "facts": {"verified": 6, "stale": 0, "unknown": 1}, "tier": "A_GROUNDED"}],
 "processes": [{"id": "mechanism:cash_burn", "label": "Monthly outflow debits cash", "kind": "mechanism|choice", "tier": "A_GROUNDED", "assumptions": [], "evidence": []}],
 "missing": [{"id": "unknown:founder_retention_process", "label": "…", "affects": ["founder_retention"]}],
 "outcomes": [{"id": "metric:cash_runway_months", "label": "Cash runway", "tier": "C_ILLUSTRATIVE", "weakest": ["mechanism:investor_process"], "computable": true, "reason": ""}],
 "edges": [{"from": "entity:company", "to": "mechanism:cash_burn"}, {"from": "mechanism:cash_burn", "to": "metric:cash_runway_months"}],
 "versions": [{"version": 1, "reason": null}, {"version": 2, "reason": "…", "diff": {}}]}
```

Both are derived views; they never mutate state and never contain other users' private profile features.

### 10.1 Thread construction algorithm
1. For every finished run, build its event signature list: `(sim_at, event_type, actor, world patch)`.
2. Collapse runs with identical signature lists into one thread; the representative is the detailed run if any.
3. Order threads: main detailed thread first (trunk), then detailed, previews, probes; ties by label.
4. For each thread after the trunk, choose the parent among already-placed threads with the **longest common prefix**; `split_index` = prefix length; `split_at` = time of the thread's first differing event (or its end if it is a strict prefix).
5. Lanes (client): DFS over the parent tree, children ordered by split time; trunk centred; siblings alternate sides.
6. Moments are emitted per thread; the client draws only moments at index ≥ `split_index` on non-trunk threads so shared moments appear once.

## 11. Edge cases
- **One thread only** — map still shows the present line, the thread, ghosts; copy invites exploring more.
- **No runs yet** — map shows present line, horizon and ghost stubs with "Explore futures" CTA.
- **Many threads (>12)** — previews collapsed into a "+N previews" bundle per parent unless *Show previews* is on; probes hidden by default.
- **Very short runs** (defer at t0) — end glyph at the present line, label offset outward.
- **Partial runs** — fading tail + grey "not simulated" zone after `ended_at`.
- **Non-numeric metrics** — not offered in the metric lens; shown in story panel as text.
- **High-stakes** — metric lens disabled for withheld metrics, ribbon explains why.
- **Revised model** — threads from different model versions never share a map; a version switcher shows "v1 futures | v2 futures".

## 12. Success criteria (pilot hypotheses, not benchmarks)
- A first-time user can say how many futures were explored, where the first split happens, and which future ran out of time/money, within 30 seconds.
- Users can point to the line separating real evidence from simulation.
- Users correctly identify one unexplored possibility and why it was not simulated.
- Users can find the weakest assumption behind an outcome in ≤ 3 taps.

## 13. Implementation plan
1. API: `/futures` and `/world-model` derived views (+ tests).
2. Design tokens, fonts, shell, navigation, theme toggle.
3. Futures map renderer (layout → orientation-agnostic geometry → SVG), lenses, cursor, focus, ghosts, legend list.
4. Thread story panel, moment sheet with *Why?*, compare mode.
5. Evidence, World model flow, Possibilities, Your Agent, Integrity.
6. Verification: desktop and 375px, dark and light, keyboard pass, reduced motion; existing API tests stay green.

Out of scope for this iteration: editing assumptions inline, running a single unexplored family on demand, multi-version overlay on one map, native app (follows this spec).
