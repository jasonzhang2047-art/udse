/* Futures — client shell, routes and views (docs/design/ui-redesign.md §4–§5). */

import {
  $, $$, api, closeSheet, errorBox, esc, fmtNum, fmtTime, fmtVal, humanize, initials, openSheet, outcomeColor, personColor,
  session, store, tierMeter, tierWords, toast, waitJob, TIERS,
} from "./util.js";
import { mountFutures, thumbnail } from "./futures.js";

const app = $("#app");
let mounted = null;

/* ------------------------------------------------------------------ shell */
const THEME_ICON = { auto: "◐", dark: "☾", light: "☀" };
function applyTheme(mode) {
  if (mode === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = mode;
  store.set("udse_theme", mode);
  $("#theme").textContent = THEME_ICON[mode];
  $("#theme").title = `Theme: ${mode}`;
}
applyTheme(store.get("udse_theme") || "auto");
$("#theme").addEventListener("click", () => {
  const cur = store.get("udse_theme") || "auto";
  applyTheme({ auto: "dark", dark: "light", light: "auto" }[cur]);
  route();
});
$("#who").addEventListener("click", () => (location.hash = "#/login"));
$(".sheet-close").addEventListener("click", closeSheet);
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

function setNav(which) { $$("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === which)); }
const loading = (msg = "Loading") => { app.innerHTML = `<p class="muted"><span class="spinner"></span> ${esc(msg)}…</p>`; };

/* ------------------------------------------------------------------ sign-in */
async function viewLogin() {
  setNav("");
  loading();
  try {
    const d = await api("/v1/dev/users");
    const groups = {};
    d.users.forEach((u) => (groups[u.fixture] = groups[u.fixture] || []).push(u));
    const names = { fundraising: "Four founders · raise now or later", restaurant: "Four friends · where to eat tonight", new_domain: "Community garden & other clean-room questions" };
    app.innerHTML = `<div class="hero"><span class="eyebrow">Development sign-in</span><h1>Whose futures are we exploring?</h1>
      <p class="lede">Pick a synthetic participant. Each person sees only their own Agent profile; worlds are shared with everyone in them.</p></div>
      <div class="grid-cards">${Object.entries(groups).map(([g, us]) => `<div class="card"><span class="eyebrow">${esc(names[g] || g)}</span>
        <div class="stack" style="margin-top:10px">${us.map((u, i) => `<button class="tl" data-token="${esc(u.token)}"><span class="avatar" style="background:${personColor(i)}">${esc(initials(u.display_name))}</span><span class="tt">${esc(u.display_name)}</span><span></span></button>`).join("")}</div></div>`).join("")}</div>
      <p class="tiny muted" style="margin-top:16px">${esc(d.warning)}</p>`;
    $$("[data-token]").forEach((b) => b.addEventListener("click", () => {
      session.token = b.dataset.token; store.set("udse_token", session.token); session.me = null; location.hash = "#/";
    }));
  } catch (e) { app.innerHTML = errorBox(e); }
}

/* ------------------------------------------------------------------ worlds */
async function viewHome() {
  setNav("home");
  loading("Loading your worlds");
  let ds;
  try { ds = await api("/v1/decisions"); } catch (e) { if (e.status === 401) { location.hash = "#/login"; return; } app.innerHTML = errorBox(e); return; }
  app.innerHTML = `<div class="hero"><span class="eyebrow">Your worlds</span>
      <div class="row between"><h1>Every decision is a small world<br>with many possible futures.</h1>
      <button class="btn primary" id="new">Ask a new question</button></div>
      <p class="lede">The engine builds only the world your decision needs, from evidence it can cite, then lets futures branch over time. It never treats a simulated event as a fact.</p></div>
    ${ds.length ? `<div class="worlds">${ds.map((d) => `<article class="card world" data-id="${esc(d.decision_id)}" tabindex="0" role="link" aria-label="${esc(d.question)}">
        <div class="thumb" data-thumb="${esc(d.decision_id)}"></div>
        <h3>${esc(d.question)}</h3>
        <div class="dmeta"><span>${esc(fmtTime(d.horizon.starts_at, "month", true))} → ${esc(fmtTime(d.horizon.ends_at, "month", true))}</span>
          <span class="avatars">${d.participants.slice(0, 5).map((p, i) => `<span class="avatar" style="background:${personColor(i)}" title="${esc(p)}">${esc(initials(p))}</span>`).join("")}</span></div>
        <div class="stats" data-stats="${esc(d.decision_id)}">${d.model_version ? `<div class="stat"><b>${d.runs}</b><span>runs</span></div>` : '<span class="pill warn">No world model yet</span>'}</div>
      </article>`).join("")}</div>` : '<div class="empty">No worlds yet. Ask a question to begin.</div>'}`;
  $("#new").addEventListener("click", newQuestionSheet);
  $$(".world").forEach((c) => {
    const go = () => (location.hash = `#/w/${c.dataset.id}/futures`);
    c.addEventListener("click", go);
    c.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  });
  for (const d of ds) {
    if (!d.model_version) continue;
    api(`/v1/decisions/${d.decision_id}/futures?light=true`).then((f) => {
      const th = $(`[data-thumb="${d.decision_id}"]`); if (th) th.innerHTML = thumbnail(f);
      const s = $(`[data-stats="${d.decision_id}"]`);
      if (s) s.innerHTML = `<div class="stat"><b>${f.threads.length}</b><span>futures explored</span></div>
        <div class="stat"><b>${f.ghosts_total}</b><span>not yet simulated</span></div>
        <div class="stat"><b>${f.evidence.gaps.length}</b><span>evidence gaps</span></div>`;
    }).catch(() => {});
  }
}

function newQuestionSheet() {
  const now = new Date(), later = new Date(now.getTime() + 365 * 864e5);
  const iso = (d) => d.toISOString().slice(0, 16);
  openSheet(`<span class="eyebrow">New world</span><h2 id="sheet-title" style="margin:6px 0 4px">Ask a question about your future</h2>
    <p class="small muted">Any kind of decision. The engine researches what it may, builds only what the decision needs, and says plainly what it cannot model.</p>
    <form class="stack" id="nq">
      <label>Your question<input type="text" name="question" required placeholder="Should we … now, or wait?"></label>
      <label>Options you're weighing (one per line)<textarea name="alts">Do it now
Wait a year</textarea></label>
      <div class="grid-cards" style="gap:10px"><label>Futures start<input type="datetime-local" name="start" value="${iso(now)}"></label>
      <label>…and run until<input type="datetime-local" name="end" value="${iso(later)}"></label></div>
      <details><summary>What you already know (optional)</summary>
        <label>Facts, one per line: <span class="mono">thing.name = value unit</span><textarea name="facts">plan.budget_usd = 5000 USD
plan.running_cost_usd_per_month = 300 USD/month</textarea></label>
        <label>Hard limits: <span class="mono">thing.name >= value</span><textarea name="limits">plan.budget_usd >= 0</textarea></label>
        <label>What you'll judge outcomes by (comma separated)<input type="text" name="metrics" value="budget_usd, budget_runway_months"></label>
      </details>
      <button class="btn primary" type="submit">Build this world</button>
      <div id="nq-status" class="jobline"></div>
    </form>`);
  $("#nq").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const o = -new Date().getTimezoneOffset(), a = Math.abs(o);
    const tz = `${o >= 0 ? "+" : "-"}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`;
    const id = "dec_" + Math.random().toString(36).slice(2, 10);
    const alts = String(f.get("alts")).split("\n").map((l) => l.trim()).filter(Boolean);
    const spec = {
      decision_id: id, question: String(f.get("question")), owner_id: session.me.user_id,
      participants: [{ agent_id: "me", user_id: session.me.user_id, display_name: session.me.display_name, profile_ref: (session.me.profile_ids || [])[0] || null }],
      participant_refs: ["me"],
      alternatives: alts.map((l) => ({ id: l.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 40) || "option", policy: l, label: l })),
      horizon: { starts_at: `${f.get("start")}:00${tz}`, ends_at: `${f.get("end")}:00${tz}` },
      initial_facts: String(f.get("facts") || "").split("\n").map((l) => l.trim()).filter((l) => l.includes("=")).map((l) => {
        const [ref, rest] = l.split("=").map((x) => x.trim()); const [val, ...unit] = (rest || "").split(/\s+/); const num = Number(val);
        return { ref, value: isNaN(num) ? val : num, unit: unit.join(" ") || (isNaN(num) ? null : "1") };
      }),
      constraints: String(f.get("limits") || "").split("\n").map((l, i) => { const m = l.trim().match(/^(\S+)\s*>=\s*(-?[\d.]+)/); return m ? { id: `limit_${i}`, ref: m[1], min: Number(m[2]), hard: true, description: l.trim() } : null; }).filter(Boolean),
      metrics: String(f.get("metrics") || "").split(",").map((x) => x.trim()).filter(Boolean),
      research_policy: { external_web: "allowed" },
    };
    const st = $("#nq-status");
    try {
      st.innerHTML = '<span class="spinner"></span> Creating the world…';
      await api("/v1/decisions", { method: "POST", body: spec });
      st.innerHTML = '<span class="spinner"></span> Researching and building a world model…';
      await waitJob((await api(`/v1/decisions/${id}/models/compile`, { method: "POST", body: { idempotency_key: `compile-${id}` } })).job_id);
      st.innerHTML = '<span class="spinner"></span> Letting futures branch…';
      const plan = await api(`/v1/decisions/${id}/experiments`, { method: "POST", body: {} });
      await waitJob((await api(`/v1/experiments/${plan.experiment_id}/runs`, { method: "POST", body: {} })).job_id);
      closeSheet(); location.hash = `#/w/${id}/futures`;
    } catch (e) { st.innerHTML = errorBox(e); }
  });
}

/* ------------------------------------------------------------------ decision shell */
const TABS = [["futures", "Futures"], ["evidence", "Evidence"], ["model", "World model"], ["possibilities", "Possibilities"], ["setup", "Setup"]];

async function viewWorld(id, tab = "futures", params = {}) {
  setNav("home");
  loading("Opening this world");
  let d, models;
  try { [d, models] = await Promise.all([api(`/v1/decisions/${id}`), api(`/v1/decisions/${id}/models`)]); }
  catch (e) {
    app.innerHTML = e.status === 403 || e.status === 404
      ? `<div class="empty card"><h2 style="margin-top:0">This world isn't shared with ${esc(session.me?.display_name || "you")}</h2><p class="muted">Each decision is visible only to its participants. Switch participant (top right) or go back to <a href="#/">your worlds</a>.</p></div>`
      : errorBox(e);
    return;
  }
  const spec = d.spec, latest = models[models.length - 1];
  const owner = d.role === "owner";
  app.innerHTML = `<div class="dhead">
      <a class="small muted" href="#/" style="text-decoration:none">← Worlds</a>
      <h1>${esc(spec.question)}</h1>
      <div class="dmeta">
        <span class="pill">${esc(fmtTime(spec.horizon.starts_at, d.scope.time_resolution, true))} → ${esc(fmtTime(spec.horizon.ends_at, d.scope.time_resolution, true))}</span>
        <span class="avatars">${spec.participants.map((p, i) => `<span class="avatar" style="background:${personColor(i)}" title="${esc(p.display_name || p.agent_id)}">${esc(initials(p.display_name || p.agent_id))}</span>`).join("")}</span>
        ${latest ? `<a class="pill ground" href="#/w/${esc(id)}/model" style="text-decoration:none">World model v${latest.version} · ${esc(latest.passed ? "checked" : "failed checks")}</a>` : '<span class="pill warn">No world model yet</span>'}
        ${owner ? `<span class="row" style="margin-left:auto"><button class="btn small ghost" id="build">${latest ? "Rebuild model" : "Build world model"}</button>
          <button class="btn small primary" id="explore" ${latest && latest.allowed_run_modes.length ? "" : "disabled"}>Explore futures</button></span>` : ""}
      </div>
      ${d.scope.high_stakes ? `<div class="ribbon">High-stakes decision (${esc(d.scope.high_stakes_reasons.join("; "))}): numbers from unverified mechanisms are withheld. Use this for questions to ask, not answers.</div>` : ""}
      <div class="jobline" id="job"></div>
    </div>
    <nav class="tabs" aria-label="World sections">${TABS.map(([k, l]) => `<a href="#/w/${esc(id)}/${k}" ${k === tab ? 'aria-current="page"' : ""}>${l}</a>`).join("")}</nav>
    <section id="tab"></section>`;
  if (owner) bindJobs(id);
  const el = $("#tab");
  try {
    if (tab === "futures") await tabFutures(el, id, latest, params);
    else if (tab === "evidence") await tabEvidence(el, id);
    else if (tab === "model") await tabModel(el, id, params.v ? Number(params.v) : null);
    else if (tab === "possibilities") await tabPossibilities(el, id);
    else tabSetup(el, spec, d.scope);
  } catch (e) { el.innerHTML = errorBox(e); }
}

function bindJobs(id) {
  const job = $("#job");
  const steps = (label) => (j) => { job.innerHTML = `<span class="spinner"></span> ${esc(label)} — ${esc(j.status.toLowerCase())}`; };
  $("#build").addEventListener("click", async () => {
    $("#build").disabled = true;
    try {
      job.innerHTML = '<span class="spinner"></span> Researching → building → checking the world model…';
      const r = await waitJob((await api(`/v1/decisions/${id}/models/compile`, { method: "POST", body: {} })).job_id, steps("Building the world model"));
      toast(r.status === "MODEL_READY" ? "World model ready" : `World model: ${humanize(r.status.toLowerCase())}`);
      location.hash = `#/w/${id}/model`; route();
    } catch (e) { job.innerHTML = errorBox(e); $("#build").disabled = false; }
  });
  $("#explore").addEventListener("click", async () => {
    $("#explore").disabled = true;
    try {
      job.innerHTML = '<span class="spinner"></span> Breadth first: exploratory previews, sensitivity probes, then detailed simulations…';
      const plan = await api(`/v1/decisions/${id}/experiments`, { method: "POST", body: {} });
      await waitJob((await api(`/v1/experiments/${plan.experiment_id}/runs`, { method: "POST", body: {} })).job_id, steps("Exploring futures"));
      toast("New futures explored"); location.hash = `#/w/${id}/futures`; route();
    } catch (e) { job.innerHTML = errorBox(e); $("#explore").disabled = false; }
  });
}

async function tabFutures(el, id, latest, params) {
  if (!latest) { el.innerHTML = '<div class="card empty">Build a world model first — it decides what can be simulated responsibly.</div>'; return; }
  const data = await api(`/v1/decisions/${id}/futures${params.v ? `?version=${params.v}` : ""}`);
  const vs = data.model.versions;
  el.innerHTML = `${vs.length > 1 ? `<div class="row" style="margin-bottom:12px"><span class="eyebrow">World model</span>
      <div class="seg">${vs.map((v) => `<button data-v="${v}" aria-pressed="${v === data.model.version}">v${v} futures</button>`).join("")}</div>
      <span class="tiny muted">Futures from different model versions are never mixed on one map.</span></div>` : ""}
    ${data.threads.length ? '<div id="fmount"></div>' : `<div class="card empty">No futures explored on model v${data.model.version} yet. Use <b>Explore futures</b> to let paths branch.</div>`}`;
  $$("[data-v]", el).forEach((b) => b.addEventListener("click", () => (location.hash = `#/w/${id}/futures?v=${b.dataset.v}`)));
  if (data.threads.length) mounted = mountFutures($("#fmount", el), { decisionId: id, data, focusRun: params.run });
}

/* ------------------------------------------------------------------ evidence */
const STATUS_GLYPH = { verified: ["✓", "Verified"], user: ["✎", "You said"], stale: ["⌛", "Stale"], unknown: ["?", "Unknown"], contested: ["⚠", "Contested / not certifiable"], hypothesis: ["◇", "Hypothesis"], quarantined: ["⛔", "Quarantined"], other: ["·", "Unverified extract"] };
function claimClass(c) {
  if (c.object === "UNKNOWN") return "unknown";
  if (c.status === "STALE") return "stale";
  if (c.status === "QUARANTINED") return "quarantined";
  if (c.status === "CONFLICTED" || c.record_type === "contextual_lead") return "contested";
  if (c.status === "HYPOTHESIS") return "hypothesis";
  if (c.status === "VERIFIED") return "verified";
  if (c.origin_kind === "USER_ASSERTED") return "user";
  return "other";
}
async function tabEvidence(el, id) {
  const [b, w] = await Promise.all([api(`/v1/decisions/${id}/evidence`), api(`/v1/decisions/${id}/world-model`).catch(() => null)]);
  const names = {};
  (w?.inputs || []).forEach((i) => { if (i.id.startsWith("entity:")) names[i.id.slice(7)] = i.label; });
  const asOf = Date.parse(b.as_of);
  const groups = {};
  b.claims.forEach((c) => (groups[c.subject] = groups[c.subject] || []).push(c));
  const gaps = b.needs.filter((n) => ["UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION"].includes(n.status));
  const fresh = (c) => {
    if (!c.observed_at || !c.expires_at) return "";
    const o = Date.parse(c.observed_at), e = Date.parse(c.expires_at);
    const f = Math.min(1, Math.max(0, (asOf - o) / Math.max(1, e - o)));
    return `<div class="fresh ${asOf > e ? "expired" : ""}" title="observed ${esc(c.observed_at.slice(0, 10))} · valid until ${esc(c.expires_at.slice(0, 10))}"><i style="width:${(asOf > e ? 1 : 1 - f) * 100}%"></i></div>`;
  };
  const host = (c) => { const u = (c.source_refs[0] || {}).uri; try { return u ? new URL(u).host : "you"; } catch { return u || "you"; } };
  el.innerHTML = `<p class="lede">What we actually know as of <b>${esc(fmtTime(b.as_of, "minute", true))}</b> — the ground every future stands on. Simulated events can never appear here.</p>
    ${b.warnings.map((x) => `<div class="banner bad" style="margin-top:12px">⚠ ${esc(x)}</div>`).join("")}
    ${gaps.length ? `<h2>Gaps that matter</h2><div class="grid-cards">${gaps.map((n) => `<div class="card"><span class="eyebrow">${esc(humanize(n.kind.toLowerCase()))} · ${esc(n.status === "ASK_USER" ? "please tell us" : "still unknown")}</span>
        <h3 style="margin:6px 0">${esc(n.question.replace(/^Find: /, ""))}</h3><p class="small muted">${esc(n.consequence_if_unresolved)}</p></div>`).join("")}</div>` : ""}
    <h2>What the world looks like now</h2>
    <div class="legendbar" style="border:0;padding:0 0 10px">${Object.values(STATUS_GLYPH).slice(0, 5).map(([g, l]) => `<span>${g} ${l}</span>`).join("")}<span class="muted">bar = freshness remaining</span></div>
    <div class="grid-cards">${Object.entries(groups).map(([subj, cs]) => `<div class="card ground-card"><div class="row between"><h3 style="margin:0">${esc(names[subj] || humanize(subj))}</h3><span class="tiny muted">${cs.length} fact${cs.length > 1 ? "s" : ""}</span></div>
      <div class="facts">${cs.map((c) => { const k = claimClass(c); return `<div class="fact ${k}" title="${esc((c.notes || []).join(" · "))}"><span class="g" aria-label="${esc(STATUS_GLYPH[k][1])}">${STATUS_GLYPH[k][0]}</span>
        <div style="min-width:0"><div class="small">${esc(humanize(c.predicate))}</div><div class="fs">${esc(STATUS_GLYPH[k][1])} · ${esc(host(c))}${c.notes && c.notes.length ? " · " + esc(c.notes[0]) : ""}</div>${fresh(c)}</div>
        <div class="fv">${fmtVal(typeof c.object === "number" ? { value: c.object, unit: c.unit } : c.object)}</div></div>`; }).join("")}</div></div>`).join("")}</div>
    ${b.rejected.length ? `<h2>Refused at the gate</h2><div class="card small">${b.rejected.map((r) => `<p>⛔ ${esc(r.claim_id || "")} — ${esc(r.reason)}</p>`).join("")}</div>` : ""}`;
}

/* ------------------------------------------------------------------ world model */
async function tabModel(el, id, version) {
  const w = await api(`/v1/decisions/${id}/world-model${version ? `?version=${version}` : ""}`);
  const node = (n, cls) => `<div class="node ${cls} tier${(n.tier || "B")[0]}" data-node="${esc(n.id)}" tabindex="0">
      <div class="row between"><span class="nt">${esc(n.label)}</span>${n.tier ? tierMeter(n.tier) : ""}</div>
      ${n.counts ? `<div class="nd">${Object.entries(n.counts).filter(([, v]) => v).map(([k, v]) => `${v} ${esc(k)}`).join(" · ")}</div>` : ""}
      ${n.kind === "choice" ? `<div class="nd">People's choice · ${esc(n.actors.join(", "))}</div>` : n.description ? `<div class="nd">${esc(n.description.length > 110 ? n.description.slice(0, 108) + "…" : n.description)}</div>` : ""}
      ${n.reuses ? `<div class="nd" style="color:var(--ok)">Reuses ${esc(n.reuses)}</div>` : ""}
      ${cls === "missing" ? `<div class="nd" style="color:var(--bad)">Missing — ${esc(n.description)}</div>` : ""}
    </div>`;
  const out = w.outcomes.map((o) => `<div class="node out" data-node="${esc(o.id)}" tabindex="0">
      <div class="row between"><span class="nt">${esc(o.label)}</span>${tierMeter(o.tier)}</div>
      <div class="nd">${o.computable ? esc(tierWords(o.tier)) : `<span style="color:var(--bad)">Withheld</span> — ${esc(o.reason)}`}</div>
      ${o.weakest.length && o.computable ? `<div class="nd">Weakest link: ${esc(o.weakest.join(", "))}</div>` : ""}</div>`).join("");
  el.innerHTML = `<p class="lede">${esc(w.model.purpose)}</p>
    <div class="row" style="margin:10px 0 4px">${w.versions.map((v) => `<a class="pill ${v.version === w.model.version ? "ground" : ""}" href="#/w/${esc(id)}/model?v=${v.version}" style="text-decoration:none">v${v.version}${v.reason ? " · " + esc(v.reason.slice(0, 40)) : ""}</a>`).join("")}
      <span class="tiny muted mono">${esc(w.model.hash.slice(0, 19))}…</span></div>
    ${(() => { const v = w.versions.find((x) => x.version === w.model.version); return v && v.diff ? `<div class="banner warn" style="margin:10px 0">Expanded from v${v.diff.from_version}: ${v.diff.mechanisms_added.length ? "added " + esc(v.diff.mechanisms_added.map(humanize).join(", ")) + ". " : ""}${v.diff.state_changed.length ? esc(v.diff.state_changed.length) + " facts changed. " : ""}${esc(v.diff.banner)}</div>` : ""; })()}
    <h2>How this world works</h2>
    <p class="small muted">Tap an outcome to trace what it depends on. Meters show evidence support — never likelihood, never averaged.</p>
    <div class="card" style="margin-top:10px"><div class="flow" id="flow">
      <svg class="edges" id="edges"></svg>
      <div class="col"><div class="coltitle">What we start from</div>${w.inputs.map((n) => node(n, "input")).join("")}</div>
      <div class="col"><div class="coltitle">How things change</div>${w.processes.map((n) => node(n, "proc")).join("")}${w.missing.filter((m) => m.status === "DISCOVERED_UNMODELED").map((m) => node({ ...m, tier: null }, "missing")).join("")}</div>
      <div class="col"><div class="coltitle">What you look at</div>${out}</div>
    </div></div>
    ${w.omissions.length ? `<h2>Left out on purpose</h2><div class="grid-cards">${w.omissions.map((o) => `<div class="card"><span class="eyebrow">${esc(o.risk)} risk</span><p style="margin-top:6px">${esc(o.description)}</p><p class="tiny muted">${esc(o.reason)}</p></div>`).join("")}</div>` : ""}
    <h2>Checks</h2><div class="card"><dl class="kv">${Object.entries(w.scorecard).filter(([k]) => k !== "interpretive_limitations").map(([k, v]) => `<dt>${esc(humanize(k))}</dt><dd class="small">${esc(v)}</dd>`).join("")}</dl>
      ${w.downgrades.length ? `<details style="margin-top:10px"><summary>${w.downgrades.length} visible downgrade${w.downgrades.length > 1 ? "s" : ""}</summary>${w.downgrades.map((g) => `<p class="small">${esc(g.id)}: ${esc(TIERS[g.from]?.short || g.from)} → ${esc(TIERS[g.to]?.short || g.to)} — ${esc(g.reason)}</p>`).join("")}</details>` : ""}</div>`;
  const flow = $("#flow"), svg = $("#edges");
  const drawEdges = (active) => {
    const fr = flow.getBoundingClientRect();
    const pos = (nid) => { const n = flow.querySelector(`[data-node="${CSS.escape(nid)}"]`); if (!n) return null; const r = n.getBoundingClientRect(); return { l: r.left - fr.left, r: r.right - fr.left, y: r.top - fr.top + r.height / 2 }; };
    const on = active ? reach(active) : null;
    svg.innerHTML = w.edges.map((e) => {
      const a = pos(e.from), b = pos(e.to); if (!a || !b) return "";
      const x1 = a.r, x2 = b.l, mx = (x1 + x2) / 2;
      const cls = (e.missing ? "missing " : "") + (on ? (on.has(e.from) && on.has(e.to) ? "on" : "off") : "");
      return `<path class="${cls}" d="M${x1},${a.y} C${mx},${a.y} ${mx},${b.y} ${x2},${b.y}"/>`;
    }).join("");
    $$(".node", flow).forEach((n) => n.classList.toggle("off", !!on && !on.has(n.dataset.node)));
    $$(".node", flow).forEach((n) => n.classList.toggle("on", !!on && n.dataset.node === active));
  };
  const reach = (target) => {
    const into = {}; w.edges.forEach((e) => (into[e.to] = into[e.to] || []).push(e.from));
    const seen = new Set([target]), stack = [target];
    while (stack.length) { const n = stack.pop(); (into[n] || []).forEach((p) => { if (!seen.has(p)) { seen.add(p); stack.push(p); } }); }
    return seen;
  };
  let active = null;
  $$(".node", flow).forEach((n) => {
    const pick = () => { active = active === n.dataset.node ? null : n.dataset.node; drawEdges(active); };
    n.addEventListener("click", pick);
    n.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
  });
  requestAnimationFrame(() => drawEdges(null));
  new ResizeObserver(() => drawEdges(active)).observe(flow);
}

/* ------------------------------------------------------------------ possibilities */
const POSS_ORDER = [
  ["DEPTH_SIMULATED", "◉", "On the map, simulated in detail"],
  ["LOW_FIDELITY_EXPLORED", "◎", "Previewed — rough, exploratory only"],
  ["REOPENED_AFTER_REVISION", "↺", "Reopened after new evidence"],
  ["DISCOVERED_UNSIMULATED", "┄", "Identified, not simulated yet"],
  ["SET_ASIDE_BUDGET", "⏸", "Set aside by budget (still possible)"],
  ["REJECTED_VALIDATED_CONSTRAINT", "✂", "Ruled out by an evidenced rule"],
];
function possReason(i) {
  const r = i.status_reason || "";
  if (/^\d+ detailed traject/.test(r) || /^\d+ screening run/.test(r)) return "";
  return r.replace(/requires unmodeled mechanism\(s\): /, "needs a model of ").replace(/not feasible under constraint ([^:]+):/, "not feasible — $1:").replace(/_/g, " ");
}
async function tabPossibilities(el, id) {
  const [cov, fut] = await Promise.all([api(`/v1/decisions/${id}/coverage`), api(`/v1/decisions/${id}/futures?light=true`).catch(() => null)]);
  const pathOf = {}, colorOf = {};
  if (fut) {
    const fam = Object.fromEntries(fut.families.map((f) => [f.key, f.color_index]));
    fut.threads.forEach((t, n) => t.run_ids.forEach((r) => { pathOf[r] = n + 1; colorOf[r] = outcomeColor(fam[t.family] ?? 0); }));
  }
  const groups = {};
  cov.items.forEach((i) => (groups[i.status] = groups[i.status] || []).push(i));
  const chips = (i) => {
    const seen = new Set(), out = [];
    i.run_ids.forEach((r) => { const n = pathOf[r]; if (n && !seen.has(n)) { seen.add(n); out.push(`<a class="pathchip" href="#/w/${esc(id)}/futures?run=${esc(r)}" style="--c:${colorOf[r]}" title="Open path ${n} on the map">${n}</a>`); } });
    return out.length ? `<div class="row" style="gap:4px;margin-top:6px"><span class="tiny muted">${out.length === 1 ? "path" : "paths"}</span>${out.join("")}</div>` : "";
  };
  el.innerHTML = `<div class="banner warn">This list is not exhaustive. Possibilities may exist that nobody has identified.</div>
    <p class="small muted" style="margin-top:10px">Searched with ${cov.discovery_methods.length} independent lenses: ${esc(cov.discovery_methods.map(humanize).join(", ").toLowerCase())}.</p>
    <div class="poss-grid">${POSS_ORDER.filter(([s]) => groups[s]).map(([s, g, title]) => `<section class="card poss-col ps-${s.toLowerCase()}"><div class="row between"><h3 style="margin:0"><span class="pg-h">${g}</span> ${esc(title)}</h3><span class="pill">${groups[s].length}</span></div>
      ${groups[s].map((i) => `<div class="poss"><span class="pg">${g}</span><div>
        <div style="font-weight:600">${esc(i.label)}</div>${possReason(i) ? `<div class="small muted">${esc(possReason(i))}</div>` : ""}
        ${i.citation.length ? `<div class="tiny mono muted">cites ${esc(i.citation.join(", "))}</div>` : ""}
        ${chips(i)}
      </div></div>`).join("")}</section>`).join("")}</div>
    ${cov.stop_rationale ? `<p class="small muted" style="margin-top:14px">${esc(cov.stop_rationale)}</p>` : ""}
    ${cov.suspected_blind_spots.length ? `<h2>Suspected blind spots</h2><div class="card small">${cov.suspected_blind_spots.map((x) => `<p>• ${esc(x)}</p>`).join("")}</div>` : ""}`;
}

function tabSetup(el, spec, scope) {
  el.innerHTML = `<div class="grid-cards">
    <div class="card"><span class="eyebrow">Options being weighed</span>${scope.alternatives.map((a) => `<div style="margin-top:10px"><b>${esc(a.label || a.id)}</b> ${a.suggested_by_system ? '<span class="pill warn">suggested by the system</span>' : ""}
      <p class="small muted">${esc(a.description || a.policy)}</p>${(a.assumptions || []).map((x) => `<p class="small">• ${esc(x.description || x.ref)} ${x.at ? `<span class="muted">(at ${esc(x.at)})</span>` : ""}</p>`).join("")}</div>`).join("")}</div>
    <div class="card"><span class="eyebrow">What you told us</span><dl class="kv" style="margin-top:8px">${spec.initial_facts.map((f) => `<dt>${esc(f.description || f.ref)}</dt><dd>${fmtVal(typeof f.value === "number" ? { value: f.value, unit: f.unit } : f.value)}</dd>`).join("") || '<dt class="muted">Nothing yet</dt><dd></dd>'}</dl>
      ${spec.constraints.length ? `<span class="eyebrow" style="display:block;margin-top:14px">Hard limits</span>${spec.constraints.map((c) => `<p class="small">⛨ ${esc(c.description || c.id)}</p>`).join("")}` : ""}</div>
    <div class="card"><span class="eyebrow">Scope</span><dl class="kv" style="margin-top:8px"><dt>Time resolution</dt><dd>${esc(scope.time_resolution)}</dd><dt>Topics</dt><dd>${esc(scope.domain_labels.join(", ") || "—")}</dd>
      <dt>Jurisdiction</dt><dd>${esc(scope.jurisdiction || "—")}</dd><dt>How the group decides</dt><dd>${esc(humanize(spec.governance_policy.kind))}</dd><dt>Outside research</dt><dd>${esc(spec.research_policy.external_web)}</dd></dl>
      ${scope.notes.map((n) => `<p class="small muted">${esc(n)}</p>`).join("")}</div></div>`;
}

/* ------------------------------------------------------------------ your agent */
async function viewAgent() {
  setNav("agent");
  loading("Loading your Agent");
  try {
    const me = session.me || (session.me = await api("/v1/me"));
    if (!me.profile_ids.length) { app.innerHTML = '<div class="empty">No Agent profile for this participant.</div>'; return; }
    const p = await api(`/v1/profiles/${me.profile_ids[0]}`);
    const cur = p.current;
    const prefs = cur.features.filter((f) => f.key.startsWith("preference."));
    const bounds = cur.features.filter((f) => f.status === "HARD_CONSTRAINT" || f.key.startsWith("constraint."));
    const beh = cur.features.filter((f) => f.key.startsWith("behavior."));
    const maxW = Math.max(1e-9, ...prefs.map((f) => Math.abs((f.value && f.value.weight) ?? 0)));
    const status = (f) => ({ CONFIRMED: "Confirmed by you", INFERRED: "Inferred", UNKNOWN: "We don't know yet", HARD_CONSTRAINT: "Hard boundary" }[f.status] || f.status);
    const pending = p.proposals.filter((x) => x.status === "PROPOSED");
    const evidenceById = Object.fromEntries(p.evidence.map((e) => [e.event_id, e]));
    app.innerHTML = `<div class="hero"><span class="eyebrow">Your Agent</span><h1>How your Agent represents you</h1>
      <p class="lede">Private to you. Simulated behaviour never changes this — only things you explicitly say, after you approve them.</p></div>
      ${pending.length ? `<h2>Suggested updates <span class="pill warn">${pending.length}</span></h2>${pending.map((x) => `<div class="card boundary"><div class="row between"><b>${esc(x.reason)}</b><span class="pill">${esc(x.mode === "shadow" ? "observed only" : "needs your approval")}</span></div>
        ${x.changes.map((c) => `<p class="small">${esc(humanize(c.key.split(".").pop()))}: ${esc(JSON.stringify(c.before?.value ?? "—"))} → <b>${esc(JSON.stringify(c.after?.value ?? "—"))}</b> ${c.after?.status === "HARD_CONSTRAINT" ? '<span class="pill">hard boundary</span>' : ""}</p>`).join("")}
        <p class="tiny muted">From: “${esc((evidenceById[x.evidence_ids[0]]?.payload?.statement) || "your feedback")}”</p>
        <div class="row"><button class="btn small primary" data-ap="${esc(x.proposal_id)}">Approve</button><button class="btn small ghost" data-rj="${esc(x.proposal_id)}">Not right</button></div></div>`).join("")}` : ""}
      <div class="grid-cards" style="margin-top:14px">
        <div class="card"><span class="eyebrow">What you care about</span>
          ${prefs.map((f) => { const w = (f.value && f.value.weight) ?? 0, unit = f.value && f.value.unit; const k = Math.log1p(Math.abs(w) * 10) / Math.log1p(maxW * 10); const pct = Math.max(6, k * 50);
            const how = `${k > 0.66 ? "Strongly " : k < 0.33 ? "Mildly " : ""}${w >= 0 ? (unit ? "prefers more" : "wants this") : (unit ? "prefers less" : "avoids this")}`;
            return `<div class="pref"><div><div style="font-weight:600">${esc(prefLabel(f.key))}</div><div class="tiny muted">${esc(how.charAt(0).toUpperCase() + how.slice(1).toLowerCase())}${unit ? ` · per ${esc(unit === "USD" ? "$" : unit)}` : ""}</div><div class="tiny muted">${esc(status(f))} · ${esc(f.evidence_refs.length)} evidence</div></div>
            <div><div class="dbar" title="weight ${esc(w)}${unit ? " per " + esc(unit) : ""} (log scale)"><i style="${w >= 0 ? `left:50%;width:${pct}%;background:var(--ok)` : `right:50%;width:${pct}%;background:var(--bad)`}"></i></div><div class="tiny muted" style="display:flex;justify-content:space-between"><span>avoid</span><span>want</span></div></div></div>`; }).join("") || '<p class="small muted">Nothing yet.</p>'}
        </div>
        <div class="stack">
          ${bounds.map((f) => `<div class="card boundary"><span class="eyebrow">⛨ Hard boundary</span><p style="margin-top:6px;font-family:var(--serif);font-size:17px">${esc(f.note && !f.note.startsWith("user-authorized") ? f.note.replace(/^from EXPLICIT_CORRECTION: /, "") : humanize(f.key.split(".").slice(1).join(" ")) + " " + JSON.stringify(f.value?.value ?? f.value))}</p><p class="tiny muted">${esc(status(f))}</p></div>`).join("")}
          <div class="card"><span class="eyebrow">How you tend to decide</span>${beh.map((f) => `<div class="pref" style="grid-template-columns:1fr auto"><div><div style="font-weight:600">${esc(humanize(f.key.split(".").pop()))}</div><div class="tiny muted">${esc(status(f))}</div></div>
            <div class="num">${f.status === "UNKNOWN" ? '<span class="pill warn">unknown</span>' : typeof f.value === "number" ? `<span class="lvl"><i style="width:${Math.round(f.value * 100)}%"></i></span> ${Math.round(f.value * 100)}%` : esc(JSON.stringify(f.value))}</div></div>`).join("")}</div>
        </div>
      </div>
      <h2>History</h2><div class="card"><ol class="rail" style="--rail:var(--ground)">${p.versions.slice().reverse().map((v) => `<li style="--mk:var(--ground)"><div class="rt">v${v.version} · ${esc(v.created_at.slice(0, 10))}</div><div class="rh">${esc(v.reason)}</div>
        ${v.version !== cur.version ? `<button class="why" data-rb="${v.version}">Restore this version</button>` : '<span class="tiny muted">current</span>'}</li>`).join("")}</ol></div>`;
    const act = async (body, path = "approvals") => { await api(`/v1/profiles/${cur.profile_id}/${path}`, { method: "POST", body }); toast("Saved"); viewAgent(); };
    $$("[data-ap]").forEach((b) => b.addEventListener("click", () => act({ proposal_id: b.dataset.ap, approve: true })));
    $$("[data-rj]").forEach((b) => b.addEventListener("click", () => act({ proposal_id: b.dataset.rj, approve: false })));
    $$("[data-rb]").forEach((b) => b.addEventListener("click", () => act({ to_version: Number(b.dataset.rb) }, "rollback")));
  } catch (e) { app.innerHTML = errorBox(e); }
}

const prefLabel = (key) => humanize(key.split(".").slice(1).join(" ")).replace(/\bpp\b/, "per person");

/* ------------------------------------------------------------------ integrity */
async function viewIntegrity() {
  setNav("integrity");
  loading("Loading the boundary log");
  try {
    const rows = await api("/v1/audit?denied_only=true");
    const zoneOf = (target) => /claims|curated|registry|raw_source|evidence/.test(target) ? "evidence" : /profile|human_evidence/.test(target) ? "profile" : /model_versions/.test(target) ? "model" : /simulation|run_/.test(target) ? "sim" : "other";
    const counts = { evidence: 0, model: 0, sim: 0, profile: 0, other: 0 };
    rows.forEach((r) => counts[zoneOf(r.target)]++);
    const zone = (k, title, sub, color) => `<div class="zone" style="background:color-mix(in srgb, ${color} 7%, var(--surface));border-color:color-mix(in srgb, ${color} 35%, var(--line))">
      <span class="eyebrow" style="color:${color}">${title}</span><p class="small muted">${sub}</p><b>${counts[k]}</b><span class="tiny muted">refused writes into this zone</span></div>`;
    app.innerHTML = `<div class="hero"><span class="eyebrow">Integrity</span><h1>Simulated futures can never become facts.</h1>
      <p class="lede">Four zones with separate write authority. Every attempt to cross a line the wrong way is refused and logged here — including attempts disguised by rewording.</p></div>
      <div class="zones">${zone("evidence", "Real evidence", "Observations, official rules, datasets — written only by the evidence pipeline.", "var(--ground)")}
        ${zone("model", "World models", "Versioned, immutable; each run pins one exact model.", "var(--o4)")}
        ${zone("sim", "Simulated futures", "Append-only event logs; can only queue research.", "var(--sim-ink)")}
        ${zone("profile", "Your profile", "Changes only from your own approved evidence.", "var(--present)")}</div>
      <h2>Refused crossings</h2>
      ${rows.length ? `<div class="card"><table><thead><tr><th>When</th><th>Who tried</th><th>What was refused</th></tr></thead><tbody>${rows.map((r) => `<tr><td class="tiny mono">${esc(r.at.slice(5, 19).replace("T", " "))}</td><td class="small">${esc(humanize(r.role))}</td><td class="small">${esc(r.reason)}<div class="tiny muted mono">${esc(r.target)}</div></td></tr>`).join("")}</tbody></table></div>`
        : '<div class="card empty">Nothing refused yet. Open any moment on the Futures map and choose “Try to treat it as real evidence”.</div>'}`;
  } catch (e) { app.innerHTML = errorBox(e); }
}

/* ------------------------------------------------------------------ router */
async function route() {
  closeSheet();
  if (mounted && mounted.destroy) { mounted.destroy(); mounted = null; }
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, query] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = Object.fromEntries(new URLSearchParams(query || ""));
  if (!session.token && parts[0] !== "login") { location.hash = "#/login"; return; }
  if (session.token && !session.me && parts[0] !== "login") {
    try { session.me = await api("/v1/me"); } catch { session.token = null; store.del("udse_token"); location.hash = "#/login"; return; }
  }
  $("#who").textContent = session.me ? session.me.display_name : "Sign in";
  window.scrollTo(0, 0);
  if (parts[0] === "login") return viewLogin();
  if (parts[0] === "w" || parts[0] === "d") return viewWorld(parts[1], parts[2] || "futures", params);
  if (parts[0] === "run" || parts[0] === "r") {
    try { const r = await api(`/v1/runs/${parts[1]}`); location.hash = `#/w/${r.manifest.decision_id}/futures?run=${parts[1]}`; } catch (e) { app.innerHTML = errorBox(e); }
    return;
  }
  if (parts[0] === "agent" || parts[0] === "profile") return viewAgent();
  if (parts[0] === "integrity" || parts[0] === "audit") return viewIntegrity();
  return viewHome();
}
window.addEventListener("hashchange", route);
route();
