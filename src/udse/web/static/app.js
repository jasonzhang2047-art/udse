/* Decision Simulator — mobile-first client for the UDSE v0.5 API.
 * Every simulated value is labelled as simulated; tiers describe evidence support, never forecast
 * accuracy; run counts are coverage statistics, never likelihoods. */
"use strict";

const $ = (sel, el = document) => el.querySelector(sel);
const app = $("#app");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
let TOKEN = store.get("udse_token");
let ME = null;

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
  if (!res.ok) {
    const err = new Error((data && (data.detail || data.error)) || res.statusText);
    err.status = res.status; err.data = data; throw err;
  }
  return data;
}

async function waitJob(jobId, onTick) {
  for (let i = 0; i < 600; i++) {
    const j = await api(`/v1/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.status === "SUCCEEDED") return j.result;
    if (j.status === "FAILED") throw new Error(j.error || "job failed");
    await new Promise((r) => setTimeout(r, 700));
  }
  throw new Error("job timed out");
}

const tierChip = (t) => t ? `<span class="chip tier-${esc(t)}" title="Evidence support, not forecast accuracy">${esc(t.split("_")[0])} · ${esc(t.split("_").slice(1).join(" ").toLowerCase())}</span>` : "";
const fmtTime = (iso, long) => {
  if (!iso || iso === "UNKNOWN") return iso || "";
  const d = iso.slice(0, 10), t = iso.slice(11, 16);
  return long ? `${d} ${t}` : t;
};
function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (v === "UNKNOWN") return '<span class="chip warn">UNKNOWN</span>';
  if (typeof v === "object" && "value" in v) return `${Number(v.value).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${esc(v.unit === "1" ? "" : v.unit)}`;
  if (typeof v === "number") return Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return esc(fmtTime(v, true));
  return esc(v);
}
const statusChip = (s) => {
  const cls = { COMPLETED_HORIZON: "ok", TERMINAL_STATE: "ok", BUDGET_EXHAUSTED: "warn", MODEL_EXECUTION_BLOCKED: "bad", FAILED: "bad",
    MODEL_READY: "ok", LIMITED_MODEL: "warn", INSUFFICIENT_MODEL: "bad", VERIFIED: "ok", STALE: "warn", CONFLICTED: "bad",
    UNVERIFIED_EXTRACT: "", HYPOTHESIS: "warn", QUARANTINED: "bad", INAPPLICABLE: "bad", RESOLVED: "ok", UNRESOLVED: "warn",
    ASK_USER: "warn", OPEN: "" }[s] ?? "";
  return `<span class="chip ${cls}">${esc(s)}</span>`;
};
const simTag = '<span class="chip sim" title="Simulation output — not a real-world fact">simulated</span>';

function setNav(which) {
  document.querySelectorAll(".tabbar a").forEach((a) => a.classList.toggle("active", a.dataset.nav === which));
}
function openSheet(html) { $("#sheet-body").innerHTML = html; $("#sheet").hidden = false; }
function closeSheet() { $("#sheet").hidden = true; }
$(".sheet-close").addEventListener("click", closeSheet);
$("#sheet").addEventListener("click", (e) => { if (e.target.id === "sheet") closeSheet(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
$("#who").addEventListener("click", () => { location.hash = "#/login"; });

function errorBox(e) { return `<div class="banner bad">${esc(e.message || e)}</div>`; }
function loading(msg = "Loading") { app.innerHTML = `<p class="muted"><span class="spinner"></span> ${esc(msg)}…</p>`; }

/* ---------------------------------------------------------------- login */
async function viewLogin() {
  setNav("");
  loading();
  try {
    const d = await api("/v1/dev/users");
    const groups = {};
    d.users.forEach((u) => (groups[u.fixture] = groups[u.fixture] || []).push(u));
    app.innerHTML = `<h1>Choose a fixture participant</h1>
      <div class="banner warn">${esc(d.warning)} Production sign-in is out of scope for this reference build.</div>
      ${Object.entries(groups).map(([g, us]) => `<h2>${esc(g.replace("_", " "))}</h2>
        <div class="grid2">${us.map((u) => `<button class="btn" data-token="${esc(u.token)}">${esc(u.display_name)}</button>`).join("")}</div>`).join("")}`;
    app.querySelectorAll("[data-token]").forEach((b) => b.addEventListener("click", () => {
      TOKEN = b.dataset.token; store.set("udse_token", TOKEN); ME = null; location.hash = "#/";
    }));
  } catch (e) { app.innerHTML = errorBox(e); }
}

/* ---------------------------------------------------------------- home */
async function viewHome() {
  setNav("home");
  loading("Loading decisions");
  try {
    const ds = await api("/v1/decisions");
    app.innerHTML = `<div class="row between"><h1>Your decisions</h1><button class="btn primary" id="new">New decision</button></div>
      <p class="muted small">Each decision is simulated over an explicit horizon. Results are hypotheses under stated assumptions, never forecasts.</p>
      ${ds.length ? ds.map((d) => `<div class="card tap" data-id="${esc(d.decision_id)}">
          <div class="row between"><strong>${esc(d.question)}</strong></div>
          <div class="row small muted" style="margin-top:6px">
            <span>${esc(fmtTime(d.horizon.starts_at, true))} → ${esc(fmtTime(d.horizon.ends_at, true))}</span>
            <span>· rev ${d.revision}</span><span>· ${esc(d.role)}</span>
          </div>
          <div class="row" style="margin-top:8px">
            ${d.model_version ? `<span class="chip">model v${d.model_version}</span><span class="chip">${esc(d.promotion_state)}</span>` : '<span class="chip warn">no model yet</span>'}
            <span class="chip">${d.runs} runs</span>
            <span class="chip">${esc(d.participants.join(", "))}</span>
          </div></div>`).join("") : '<div class="empty">No decisions yet.</div>'}`;
    app.querySelectorAll("[data-id]").forEach((c) => c.addEventListener("click", () => (location.hash = `#/d/${c.dataset.id}`)));
    $("#new").addEventListener("click", newDecisionSheet);
  } catch (e) {
    if (e.status === 401) { location.hash = "#/login"; return; }
    app.innerHTML = errorBox(e);
  }
}

function newDecisionSheet() {
  const now = new Date(); const later = new Date(now.getTime() + 365 * 864e5);
  const iso = (d) => d.toISOString().slice(0, 16);
  openSheet(`<h2>New decision</h2>
    <p class="muted small">Any category. The engine researches what it may, builds only the world the decision needs, and abstains where it cannot represent a mechanism responsibly.</p>
    <form class="stack" id="nd">
      <label>Question<input type="text" name="question" required placeholder="Should we … now or later?"></label>
      <div class="grid2"><label>Horizon start<input type="datetime-local" name="start" value="${iso(now)}"></label>
      <label>Horizon end<input type="datetime-local" name="end" value="${iso(later)}"></label></div>
      <label>Alternatives (one per line: id | label)<textarea name="alts">now | Do it now
later | Do it later</textarea></label>
      <label>Known facts (ref = value unit, one per line)<textarea name="facts">plan.budget_usd = 5000 USD
plan.running_cost_usd_per_month = 300 USD/month</textarea></label>
      <label>Hard limits (ref >= value)<textarea name="limits">plan.budget_usd >= 0</textarea></label>
      <label>Metrics you care about (comma separated)<input type="text" name="metrics" value="budget_usd, budget_runway_months"></label>
      <button class="btn primary" type="submit">Create and build model</button>
      <div id="nd-status"></div>
    </form>`);
  $("#nd").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const tz = (() => { const o = -new Date().getTimezoneOffset(); const s = o >= 0 ? "+" : "-"; const a = Math.abs(o); return `${s}${String(Math.floor(a / 60)).padStart(2, "0")}:${String(a % 60).padStart(2, "0")}`; })();
    const id = "dec_" + Math.random().toString(36).slice(2, 10);
    const facts = String(f.get("facts")).split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [ref, rest] = l.split("=").map((x) => x.trim());
      const [val, ...unit] = (rest || "").split(/\s+/);
      const num = Number(val);
      return { ref, value: isNaN(num) ? val : num, unit: unit.join(" ") || (isNaN(num) ? null : "1") };
    });
    const spec = {
      decision_id: id, question: String(f.get("question")), owner_id: ME.user_id,
      participants: [{ agent_id: "me", user_id: ME.user_id, display_name: ME.display_name, profile_ref: (ME.profile_ids || [])[0] || null }],
      participant_refs: ["me"],
      alternatives: String(f.get("alts")).split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        const [aid, label] = l.split("|").map((x) => x.trim()); return { id: aid.replace(/\W+/g, "_"), policy: aid, label: label || aid };
      }),
      horizon: { starts_at: `${f.get("start")}:00${tz}`, ends_at: `${f.get("end")}:00${tz}` },
      initial_facts: facts,
      constraints: String(f.get("limits")).split("\n").map((l) => l.trim()).filter(Boolean).map((l, i) => {
        const m = l.match(/^(\S+)\s*>=\s*(-?[\d.]+)/); return m ? { id: `limit_${i}`, ref: m[1], min: Number(m[2]), hard: true, description: l } : null;
      }).filter(Boolean),
      metrics: String(f.get("metrics")).split(",").map((x) => x.trim()).filter(Boolean),
      research_policy: { external_web: "allowed" },
    };
    const st = $("#nd-status");
    try {
      st.innerHTML = '<p class="muted"><span class="spinner"></span> Creating decision…</p>';
      await api("/v1/decisions", { method: "POST", body: spec });
      st.innerHTML = '<p class="muted"><span class="spinner"></span> Researching and compiling a decision-scoped model…</p>';
      const job = await api(`/v1/decisions/${id}/models/compile`, { method: "POST", body: { idempotency_key: `compile-${id}` } });
      await waitJob(job.job_id);
      closeSheet(); location.hash = `#/d/${id}/model`;
    } catch (e) { st.innerHTML = errorBox(e); }
  });
}

/* ---------------------------------------------------------------- decision */
async function viewDecision(id, tab = "overview") {
  setNav("home");
  loading("Loading decision");
  let d, models, runs, cov, ev;
  try {
    [d, models, runs] = await Promise.all([api(`/v1/decisions/${id}`), api(`/v1/decisions/${id}/models`), api(`/v1/decisions/${id}/runs`)]);
    cov = await api(`/v1/decisions/${id}/coverage`).catch(() => null);
    ev = await api(`/v1/decisions/${id}/evidence`).catch(() => null);
  } catch (e) { app.innerHTML = errorBox(e); return; }
  const spec = d.spec, latest = models[models.length - 1];
  const isOwner = d.role === "owner";
  const tabs = [["overview", "Trajectories"], ["model", "Model"], ["evidence", "Evidence"], ["coverage", "Coverage"], ["setup", "Setup"]];
  app.innerHTML = `<p class="small"><a href="#/">← Decisions</a></p>
    <h1>${esc(spec.question)}</h1>
    <div class="row small muted"><span>${esc(fmtTime(spec.horizon.starts_at, true))} → ${esc(fmtTime(spec.horizon.ends_at, true))}</span>
      <span>· revision ${spec.revision}</span>${latest ? `<span>· model v${latest.version}</span>` : ""}
      ${d.scope.high_stakes ? '<span class="chip bad">high-stakes: conditional analysis only</span>' : ""}</div>
    ${isOwner ? `<div class="row" style="margin-top:10px">
      <button class="btn" id="build">${latest ? "Rebuild model" : "Build model"}</button>
      <button class="btn primary" id="runexp" ${latest && latest.allowed_run_modes.length ? "" : "disabled"}>Run experiments</button></div>
      <div id="jobstatus"></div>` : ""}
    <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" aria-selected="${k === tab}" data-tab="${k}">${l}</button>`).join("")}</div>
    <div id="tabbody"></div>`;
  app.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => (location.hash = `#/d/${id}/${b.dataset.tab}`)));
  if (isOwner) {
    $("#build").addEventListener("click", async () => {
      const js = $("#jobstatus"); $("#build").disabled = true;
      try {
        js.innerHTML = '<p class="muted"><span class="spinner"></span> Planning research, acquiring evidence, synthesising and validating the model…</p>';
        const job = await api(`/v1/decisions/${id}/models/compile`, { method: "POST", body: {} });
        await waitJob(job.job_id); location.hash = `#/d/${id}/model`; route();
      } catch (e) { js.innerHTML = errorBox(e); $("#build").disabled = false; }
    });
    $("#runexp").addEventListener("click", async () => {
      const js = $("#jobstatus"); $("#runexp").disabled = true;
      try {
        js.innerHTML = '<p class="muted"><span class="spinner"></span> Breadth screening, sensitivity probes, then detailed runs…</p>';
        const plan = await api(`/v1/decisions/${id}/experiments`, { method: "POST", body: {} });
        const job = await api(`/v1/experiments/${plan.experiment_id}/runs`, { method: "POST", body: {} });
        await waitJob(job.job_id, (j) => { js.innerHTML = `<p class="muted"><span class="spinner"></span> Experiment ${esc(j.status.toLowerCase())}…</p>`; });
        location.hash = `#/d/${id}/overview`; route();
      } catch (e) { js.innerHTML = errorBox(e); $("#runexp").disabled = false; }
    });
  }
  const body = $("#tabbody");
  if (tab === "overview") body.innerHTML = renderTrajectories(id, runs);
  if (tab === "model") body.innerHTML = latest ? await renderModel(id, latest, models) : '<div class="empty">No model yet. Build one to see what is modeled, assumed and omitted.</div>';
  if (tab === "evidence") body.innerHTML = ev ? renderEvidence(ev) : '<div class="empty">No evidence bundle yet.</div>';
  if (tab === "coverage") body.innerHTML = cov ? renderCoverage(cov) : '<div class="empty">No coverage frontier yet.</div>';
  if (tab === "setup") body.innerHTML = renderSetup(spec, d.scope);
  if (tab === "overview") bindCompare(id, runs);
  if (tab === "model") body.querySelectorAll("[data-ver]").forEach((b) => b.addEventListener("click", async () => {
    body.innerHTML = await renderModel(id, models.find((m) => m.version === Number(b.dataset.ver)), models);
  }));
}

function renderTrajectories(id, runs) {
  if (!runs.length) return '<div class="empty">No runs yet. Run experiments to explore trajectories.</div>';
  const depth = runs.filter((r) => r.fidelity_mode === "depth");
  const other = runs.filter((r) => r.fidelity_mode !== "depth");
  const card = (r) => `<div class="card tap" data-run="${esc(r.run_id)}">
      <div class="row between"><strong>${esc(r.name)}</strong>${statusChip(r.status)}</div>
      <p class="small">${esc(r.synopsis)}</p>
      <div class="row">${r.outcome ? `<span class="chip">${esc(r.outcome)}</span>` : ""}${r.horizon_reached ? '<span class="chip ok">horizon reached</span>' : ""}
        ${r.weakest_tier ? tierChip(r.weakest_tier) : ""}${r.deadline_disclosure === "DEADLINE_AWARE" ? '<span class="chip">deadline disclosed</span>' : ""}${simTag}</div>
      ${r.caveats.length ? `<ul class="small muted" style="margin:8px 0 0 18px;padding:0">${r.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
    </div>`;
  return `<div class="banner sim">These are simulated trajectories under explicit assumptions. Counts of runs are coverage statistics inside one model — never likelihoods.</div>
    <h2>Detailed trajectories</h2>${depth.length ? depth.map(card).join("") : '<p class="muted">None yet.</p>'}
    <h2>Compare two paths</h2>
    <div class="card"><div class="grid2"><select id="ca">${runs.map((r) => `<option value="${esc(r.run_id)}">${esc(r.name)} · ${esc(r.fidelity_mode)}</option>`).join("")}</select>
      <select id="cb">${runs.map((r, i) => `<option value="${esc(r.run_id)}" ${i === 1 ? "selected" : ""}>${esc(r.name)} · ${esc(r.fidelity_mode)}</option>`).join("")}</select></div>
      <div class="row" style="margin-top:8px"><button class="btn" id="cmp">Show divergence</button></div></div>
    <h2>Exploratory previews <span class="chip">${other.length}</span></h2>${other.map(card).join("")}`;
}
function bindCompare(id, runs) {
  app.querySelectorAll("[data-run]").forEach((c) => c.addEventListener("click", () => (location.hash = `#/r/${c.dataset.run}`)));
  const b = $("#cmp"); if (b) b.addEventListener("click", () => (location.hash = `#/c/${$("#ca").value}/${$("#cb").value}`));
}

async function renderModel(id, m, models) {
  const full = await api(`/v1/decisions/${id}/models/${m.version}`);
  const model = full.model, rep = full.report, sc = rep.scorecard;
  const reuse = model.reuse_manifest || [];
  return `<div class="row">${models.map((x) => `<button class="btn small" data-ver="${x.version}" ${x.version === m.version ? 'style="border-color:var(--accent)"' : ""}>v${x.version}</button>`).join("")}</div>
    ${m.parent_version ? `<div class="banner warn">Model expanded from v${m.parent_version}: ${esc(m.revision_reason)}. Prior scenarios are not exhaustive and numeric comparisons across versions require caution.</div>` : ""}
    <div class="card"><div class="row between"><strong>Model v${m.version}</strong>${statusChip(m.passed ? "MODEL_READY" : "INSUFFICIENT_MODEL")}</div>
      <p class="small mono">${esc(m.hash)}</p><p class="small muted">${esc(model.purpose)}</p>
      <div class="row"><span class="chip">${esc(m.promotion_state)}</span>${m.allowed_run_modes.map((x) => `<span class="chip">${esc(x)}</span>`).join("")}</div></div>
    <h2>Fidelity by metric</h2>
    <div class="card"><dl class="kv">${Object.entries(rep.metric_fidelity).map(([k, t]) => `<dt>${esc(k)}${rep.non_computable_metrics[k] ? `<div class="small">${esc(rep.non_computable_metrics[k])}</div>` : ""}</dt><dd>${tierChip(t)}</dd>`).join("")}</dl>
      <p class="small muted">Each metric shows the weakest tier along its dependency path. Scores are never averaged.</p></div>
    <h2>Scorecard</h2>
    <div class="card"><dl class="kv">${["schema_validity", "mechanism_coverage", "empirical_support", "parameter_identifiability", "transfer_applicability", "trajectory_stability", "structural_omission_risk"].map((k) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd class="small">${esc(sc[k])}</dd>`).join("")}</dl>
      <ul class="small muted" style="margin:8px 0 0 18px;padding:0">${(sc.interpretive_limitations || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>
    <h2>Mechanisms</h2>
    ${model.mechanisms.map((x) => `<div class="card"><div class="row between"><strong>${esc(x.id)}</strong>${tierChip(x.epistemic_tier)}</div>
       <p class="small">${esc(x.description)}</p><div class="row small muted"><span class="chip">${esc(x.operator)}</span>${x.registry_ref ? `<span class="chip ok">reuses ${esc(x.registry_ref.entry_id)}@${esc(x.registry_ref.version)}</span>` : ""}
       ${(x.assumptions || []).map((a) => `<span class="chip warn">${esc(a)}</span>`).join("")}</div></div>`).join("")}
    ${rep.downgrades.length ? `<h2>Visible downgrades</h2><div class="card tablewrap"><table><tr><th>Element</th><th>From → to</th><th>Why</th></tr>${rep.downgrades.map((g) => `<tr><td>${esc(g.id)}</td><td>${esc(g.from)} → ${esc(g.to)}</td><td>${esc(g.reason)}</td></tr>`).join("")}</table></div>` : ""}
    <h2>What is not modeled</h2>
    <div class="card">${model.structural_unknowns.map((u) => `<p><span class="chip ${u.impact === "high" ? "bad" : "warn"}">${esc(u.impact)}</span> <strong>${esc(u.id)}</strong> — ${esc(u.description)} <span class="chip">${esc(u.status)}</span></p>`).join("")}
      ${model.omissions.map((o) => `<p><span class="chip">omitted</span> ${esc(o.description)} <span class="muted small">(${esc(o.reason)})</span></p>`).join("")}</div>
    <h2>Reuse and transfer checks</h2>
    <div class="card tablewrap"><table><tr><th>Registry entry</th><th>Transfer gate</th><th>Reasons</th></tr>${reuse.map((r) => `<tr><td>${esc(r.entry)}</td><td>${r.used ? '<span class="chip ok">used</span>' : r.transfer_ok ? '<span class="chip">compatible</span>' : '<span class="chip bad">rejected</span>'}</td><td class="small">${esc((r.reasons || []).join("; "))}</td></tr>`).join("") || '<tr><td colspan="3" class="muted">No reused components</td></tr>'}</table></div>`;
}

function renderEvidence(b) {
  const label = (c) => c.record_type === "contextual_lead" ? '<span class="chip warn">contextual lead</span>' : "";
  return `<div class="banner sim">Evidence describes the real world as of retrieval. Simulated events can never enter this list.</div>
    ${b.warnings.map((w) => `<div class="banner warn">${esc(w)}</div>`).join("")}
    <h2>Research needs</h2>
    ${[...b.needs].sort((x, y) => ["UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION", "OPEN", "RESOLVED"].indexOf(x.status) - ["UNRESOLVED", "ASK_USER", "SKIPPED_PERMISSION", "OPEN", "RESOLVED"].indexOf(y.status) || x.priority - y.priority).map((n) => `<div class="card"><div class="row between"><strong class="small">${esc(n.question)}</strong>${statusChip(n.status)}</div>
      <div class="row small muted"><span class="chip">${esc(n.kind)}</span><span class="chip">priority ${n.priority}</span><span>${esc(n.priority_rationale)}</span></div>
      ${n.status !== "RESOLVED" ? `<p class="small">${esc(n.consequence_if_unresolved)}</p>` : ""}</div>`).join("")}
    <h2>Claims</h2>
    <div class="card tablewrap"><table><tr><th>Claim</th><th>Value</th><th>Status</th><th>Origin · freshness</th></tr>
    ${b.claims.map((c) => `<tr><td><strong>${esc(c.subject)}</strong>.${esc(c.predicate)}<div class="small muted">${esc((c.source_refs[0] || {}).uri || "user-provided")}</div>${(c.notes || []).map((n) => `<div class="small muted">• ${esc(n)}</div>`).join("")}</td>
      <td>${fmtVal(c.object)} ${esc(c.unit || "")}</td><td>${statusChip(c.status)} ${label(c)}</td>
      <td class="small">${esc(c.origin_kind)}<br>observed ${esc(fmtTime(c.observed_at, true))}${c.expires_at ? `<br>expires ${esc(fmtTime(c.expires_at, true))}` : ""}</td></tr>`).join("")}</table></div>
    ${b.conflicts.length ? `<h2>Conflicts</h2>${b.conflicts.map((c) => `<div class="card small">${esc(c.subject)}.${esc(c.predicate)}: ${esc(c.resolution)} (${esc(c.claims.join(", "))})</div>`).join("")}` : ""}
    ${b.rejected.length ? `<h2>Rejected at ingestion</h2>${b.rejected.map((r) => `<div class="card small">${esc(r.claim_id || "")} ${esc(r.reason)}</div>`).join("")}` : ""}`;
}

function renderCoverage(cov) {
  const order = ["DEPTH_SIMULATED", "LOW_FIDELITY_EXPLORED", "REOPENED_AFTER_REVISION", "DISCOVERED_UNSIMULATED", "SET_ASIDE_BUDGET", "REJECTED_VALIDATED_CONSTRAINT"];
  const groups = {};
  cov.items.forEach((i) => (groups[i.status] = groups[i.status] || []).push(i));
  return `<div class="banner warn">${esc(cov.not_yet_discovered_warning)}</div>
    <p class="small muted">Discovery methods: ${esc(cov.discovery_methods.join(", "))}</p>
    ${cov.stop_rationale ? `<p class="small">${esc(cov.stop_rationale)}</p>` : ""}
    ${order.filter((s) => groups[s]).map((s) => `<h2>${esc(groups[s][0].ui_label)} <span class="chip">${groups[s].length}</span></h2>
      ${groups[s].map((i) => `<div class="card"><div class="row between"><strong>${esc(i.label)}</strong><span class="chip">${esc(i.family_kind)}</span></div>
        ${i.description ? `<p class="small muted">${esc(i.description)}</p>` : ""}<p class="small">${esc(i.status_reason)}</p>
        ${i.citation.length ? `<p class="small">Citation: <span class="mono">${esc(i.citation.join(", "))}</span></p>` : ""}
        ${i.run_ids.length ? `<div class="row">${i.run_ids.slice(0, 4).map((r) => `<a class="chip" href="#/r/${esc(r)}">${esc(r.slice(-6))}</a>`).join("")}</div>` : ""}</div>`).join("")}`).join("")}
    <h2>Suspected blind spots</h2><div class="card small">${cov.suspected_blind_spots.map((s) => `<p>• ${esc(s)}</p>`).join("")}</div>`;
}

function renderSetup(spec, scope) {
  return `<h2>Alternatives</h2>${scope.alternatives.map((a) => `<div class="card"><strong>${esc(a.label || a.id)}</strong> ${a.suggested_by_system ? '<span class="chip warn">suggested by system</span>' : ""}
      <p class="small muted">${esc(a.description || a.policy)}</p>${(a.assumptions || []).map((x) => `<p class="small">• ${esc(x.ref)} ${x.value !== null && x.value !== undefined ? "= " + esc(x.value) : "Δ " + esc(x.delta)} ${esc(x.unit || "")} ${x.at ? "at " + esc(x.at) : ""}</p>`).join("")}</div>`).join("")}
    <h2>Known facts</h2><div class="card"><dl class="kv">${spec.initial_facts.map((f) => `<dt>${esc(f.ref)}<div class="small">${esc(f.description)}</div></dt><dd>${fmtVal(f.value)} ${esc(f.unit || "")}</dd>`).join("")}</dl></div>
    <h2>Constraints</h2><div class="card">${spec.constraints.map((c) => `<p class="small">${c.hard ? '<span class="chip bad">hard</span>' : '<span class="chip">soft</span>'} ${esc(c.description || c.id)}</p>`).join("") || '<p class="muted small">None</p>'}</div>
    <h2>Scope</h2><div class="card"><dl class="kv"><dt>Resolution</dt><dd>${esc(scope.time_resolution)}</dd><dt>Domain labels</dt><dd>${esc(scope.domain_labels.join(", "))}</dd>
      <dt>Jurisdiction</dt><dd>${esc(scope.jurisdiction || "—")}</dd><dt>Governance</dt><dd>${esc(spec.governance_policy.kind)}</dd><dt>External research</dt><dd>${esc(spec.research_policy.external_web)}</dd></dl>
      ${scope.notes.map((n) => `<p class="small muted">${esc(n)}</p>`).join("")}</div>`;
}

/* ---------------------------------------------------------------- run timeline */
async function viewRun(runId) {
  setNav("home");
  loading("Loading trajectory");
  let tl, run;
  try { [tl, run] = await Promise.all([api(`/v1/runs/${runId}/timeline`), api(`/v1/runs/${runId}`)]); }
  catch (e) { app.innerHTML = errorBox(e); return; }
  const metrics = Object.entries(tl.metric_meta).filter(([, m]) => m.computable);
  const numeric = metrics.filter(([k]) => tl.series[k] && tl.series[k].some((p) => p.value && typeof p.value === "object"));
  let filter = "all", sel = tl.events.length - 1, metric = (numeric[0] || [])[0];
  const decisionId = run.manifest.decision_id;
  app.innerHTML = `<p class="small"><a href="#/d/${esc(decisionId)}/overview">← Trajectories</a></p>
    <h1>${esc(tl.label || tl.policy_id)}</h1>
    <div class="row">${statusChip(tl.status)}${tl.terminal_outcome ? `<span class="chip">${esc(tl.terminal_outcome)}</span>` : ""}
      <span class="chip">${esc(tl.fidelity_mode)}</span>${tl.deadline_disclosure !== "NOT_APPLICABLE" ? `<span class="chip">${esc(tl.deadline_disclosure)}</span>` : ""}${simTag}</div>
    <p class="small muted">${esc(tl.reason || "")}</p>
    ${tl.grayed_after && !tl.horizon_reached ? `<div class="banner warn">Horizon not reached — simulation stopped at ${esc(fmtTime(tl.last_simulated_at, true))}. Later periods are grayed out and never fabricated.</div>` : ""}
    ${(run.result && run.result.outcome_flags.length) ? `<div class="row">${run.result.outcome_flags.map((f) => `<span class="chip warn">${esc(f)}</span>`).join("")}</div>` : ""}
    <div class="card">
      <div class="row between"><label class="small muted" for="metric">Metric</label>
        <select id="metric" style="width:auto">${numeric.map(([k, m]) => `<option value="${esc(k)}">${esc(m.label)}</option>`).join("")}</select></div>
      <div id="chart"></div>
      <p class="small muted">${esc(tl.resolution_note)}</p>
      <input class="scrub" id="scrub" type="range" min="0" max="${Math.max(0, tl.events.length - 1)}" value="${sel}" aria-label="Scrub simulated time">
      <div id="statepanel"></div>
    </div>
    <div class="tabs" id="filters">${[["all", "All events"], ["agents", "Agents"], ["world", "World processes"], ["info", "Information"]].map(([k, l]) => `<button data-f="${k}" aria-selected="${k === filter}">${l}</button>`).join("")}</div>
    <ul class="events" id="events"></ul>
    <h2>Your reaction (real human evidence)</h2>
    <div class="card stack small">
      <p class="muted">Only what you explicitly say counts as evidence about you. Viewing or scrubbing is not interpreted as a preference.</p>
      <div class="row"><button class="btn small" id="prefer">I prefer this path</button><button class="btn small" id="correct">My Agent doesn't represent me…</button></div>
      <div id="fbstatus"></div>
    </div>`;
  const evs = tl.events;
  const keep = (e) => filter === "all" || (filter === "agents" && e.kind === "AGENT_DECISION") ||
    (filter === "world" && ["MECHANISM_TICK", "GATE_FIRE", "EXOGENOUS", "CONSTRAINT_BLOCKED", "POLICY_INIT"].includes(e.kind)) ||
    (filter === "info" && e.kind === "DOMAIN_EVENT");
  function drawEvents() {
    $("#events").innerHTML = evs.filter(keep).filter((e) => !(e.kind === "MECHANISM_TICK" && !e.changes.length)).map((e) =>
      `<li data-seq="${e.seq}" class="${e.seq === evs[sel]?.seq ? "sel" : ""} ${e.seq > evs[sel]?.seq ? "after" : ""}">
        <div class="t">${esc(fmtTime(e.sim_at, tl.horizon_start.slice(0, 10) !== tl.horizon_end.slice(0, 10)))}</div>
        <div><div class="s">${esc(e.summary)} ${e.rejection_code ? '<span class="chip bad">rejected</span>' : ""} ${e.tier && e.tier !== "A_GROUNDED" ? tierChip(e.tier) : ""}</div>
        ${e.rationale ? `<div class="r">“${esc(e.rationale.slice(0, 180))}${e.rationale.length > 180 ? "…" : ""}”</div>` : ""}</div></li>`).join("");
    $("#events").querySelectorAll("li").forEach((li) => li.addEventListener("click", () => eventSheet(runId, evs.find((x) => x.seq === Number(li.dataset.seq)))));
  }
  async function drawState() {
    const e = evs[sel]; if (!e) return;
    const st = await api(`/v1/runs/${runId}/state?seq=${e.seq}`);
    const entries = Object.entries(st.world).filter(([, v]) => v.visibility !== "hidden");
    $("#statepanel").innerHTML = `<p class="small"><strong>State after event ${e.seq}</strong> · simulated ${esc(fmtTime(e.sim_at, true))} ${simTag}</p>
      <dl class="kv small">${entries.slice(0, 40).map(([k, v]) => `<dt>${esc(v.label)}</dt><dd>${fmtVal(v.value)} ${v.unit && v.unit !== "1" && typeof v.value === "number" ? esc(v.unit) : ""}</dd>`).join("")}</dl>`;
  }
  function drawChart() {
    $("#chart").innerHTML = metric ? stepChart(tl, metric, evs[sel]) : '<p class="muted small">No numeric metric to chart.</p>';
  }
  $("#metric")?.addEventListener("change", (e) => { metric = e.target.value; drawChart(); });
  $("#scrub").addEventListener("input", (e) => { sel = Number(e.target.value); drawChart(); drawEvents(); });
  $("#scrub").addEventListener("change", drawState);
  $("#filters").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    filter = b.dataset.f; $("#filters").querySelectorAll("button").forEach((x) => x.setAttribute("aria-selected", x === b)); drawEvents();
  }));
  $("#prefer").addEventListener("click", () => sendFeedback(decisionId, runId, "EXPLICIT_SELECTION", { statement: "I prefer this path", preferred_run: runId }));
  $("#correct").addEventListener("click", () => correctionSheet(decisionId, runId));
  drawChart(); drawEvents(); drawState();
}

function stepChart(tl, metric, cursorEvent) {
  const pts = (tl.series[metric] || []).filter((p) => p.value && typeof p.value === "object");
  if (!pts.length) return '<p class="muted small">No values.</p>';
  const W = 640, H = 180, P = 34;
  const t0 = Date.parse(tl.horizon_start), t1 = Date.parse(tl.horizon_end), tl_ = Date.parse(tl.last_simulated_at);
  const xs = (t) => P + (W - 2 * P) * ((t - t0) / Math.max(1, t1 - t0));
  const vals = pts.map((p) => p.value.value);
  let lo = Math.min(...vals, 0), hi = Math.max(...vals); if (hi === lo) hi = lo + 1;
  const ys = (v) => H - P + 6 - (H - 2 * P) * ((v - lo) / (hi - lo));
  let d = "";
  pts.forEach((p, i) => {
    const x = xs(Date.parse(p.sim_at)), y = ys(p.value.value);
    d += i === 0 ? `M${x},${y}` : ` H${x} V${y}`;
  });
  d += ` H${xs(tl_)}`;
  const cx = cursorEvent ? xs(Date.parse(cursorEvent.sim_at)) : null;
  const unit = pts[0].value.unit === "1" ? "" : pts[0].value.unit;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(metric)} over simulated time">
    ${tl_ < t1 && !tl.horizon_reached ? `<rect class="gray" x="${xs(tl_)}" y="${P - 10}" width="${W - P - xs(tl_)}" height="${H - 2 * P + 16}"><title>not simulated</title></rect>` : ""}
    <line class="axis" x1="${P}" y1="${H - P + 6}" x2="${W - P}" y2="${H - P + 6}"/>
    <path class="line" d="${d}"/>
    ${pts.map((p) => `<circle class="pt" r="2.5" cx="${xs(Date.parse(p.sim_at))}" cy="${ys(p.value.value)}"><title>${esc(fmtTime(p.sim_at, true))}: ${esc(p.value.value)}</title></circle>`).join("")}
    ${cx !== null ? `<line class="cursor" x1="${cx}" y1="${P - 10}" x2="${cx}" y2="${H - P + 6}"/>` : ""}
    <text x="${P}" y="${H - 6}">${esc(fmtTime(tl.horizon_start, true))}</text>
    <text x="${W - P}" y="${H - 6}" text-anchor="end">${esc(fmtTime(tl.horizon_end, true))}</text>
    <text x="${P}" y="${P - 14}">${esc(Number(hi).toLocaleString(undefined, { maximumFractionDigits: 1 }))} ${esc(unit)}</text>
    <text x="${P}" y="${H - P + 2}">${esc(Number(lo).toLocaleString(undefined, { maximumFractionDigits: 1 }))}</text>
  </svg>`;
}

async function eventSheet(runId, e) {
  openSheet(`<h2>${esc(e.summary)}</h2>
    <div class="row">${simTag}<span class="chip">${esc(e.kind)}</span>${e.tier ? tierChip(e.tier) : ""}<span class="chip">seq ${e.seq}</span><span class="chip">${esc(fmtTime(e.sim_at, true))}</span></div>
    ${e.rationale ? `<h3>Agent's stated rationale</h3><p>${esc(e.rationale)}</p>` : ""}
    ${e.uncertainty ? `<p class="small muted">Uncertainty: ${esc(e.uncertainty)}</p>` : ""}
    ${e.policy_kind ? `<p class="small muted">Policy: ${esc(e.policy_kind)}</p>` : ""}
    ${e.changes.length ? `<h3>State changes</h3><div class="tablewrap"><table>${e.changes.map((c) => `<tr><td>${esc(c.ref)}</td><td>${fmtVal(c.before)} → ${fmtVal(c.after)}</td></tr>`).join("")}</table></div>` : ""}
    ${e.draws.length ? `<h3>Random draws (recorded)</h3>${e.draws.map((d) => `<p class="small mono">${esc(d.stream)} · ${esc(JSON.stringify(d.value))} · ${esc(JSON.stringify(d.distribution))}</p>`).join("")}` : ""}
    ${e.assumptions.length ? `<h3>Assumptions</h3>${e.assumptions.map((a) => `<p class="small">• ${esc(a)}</p>`).join("")}` : ""}
    <div class="row" style="margin-top:12px"><button class="btn" id="why">Why did this happen?</button><button class="btn" id="promo">Try to promote as real evidence</button></div>
    <div id="sheetx"></div>`);
  $("#why").addEventListener("click", async () => {
    const w = await api(`/v1/runs/${runId}/why/${e.seq}`);
    $("#sheetx").innerHTML = `<h3>Causal lineage (from committed events)</h3><div class="chain">${w.chain.map((s) => `<div class="step">
      <div class="small muted">${esc(s.category)} · ${esc(fmtTime(s.sim_at, true))}</div><div>${esc(s.text)}</div>
      ${s.rationale ? `<div class="small">Rationale: ${esc(s.rationale)}</div>` : ""}
      ${(s.mechanisms || []).map((m) => `<div class="small">Mechanism ${esc(m.id)} ${tierChip(m.tier)} — ${esc(m.description)}</div>`).join("")}
      ${(s.random_draws || []).map((d) => `<div class="small">Draw from ${esc(d.stream)}: ${esc(JSON.stringify(d.value))}</div>`).join("")}</div>`).join("")}</div>
      <h3>Limitations</h3>${w.limitations.map((l) => `<p class="small">• ${esc(l)}</p>`).join("")}`;
  });
  $("#promo").addEventListener("click", async () => {
    const r = await api(`/v1/runs/${runId}/events/${e.seq}/promotion-check`);
    $("#sheetx").innerHTML = `<div class="banner ${r.all_refused ? "ok" : "bad"}">${r.all_refused ? "Every promotion path was refused." : "A promotion path was accepted — boundary breach!"} Layer A unchanged: ${r.layer_a_hash_unchanged ? "yes" : "NO"}</div>
      ${r.attempts.map((a) => `<p class="small">${a.refused ? '<span class="chip ok">refused</span>' : '<span class="chip bad">accepted</span>'} <strong>${esc(a.path)}</strong><br><span class="muted">${esc(a.detail)}</span></p>`).join("")}`;
  });
}

async function sendFeedback(decisionId, runId, signal, payload) {
  const st = $("#fbstatus") || $("#sheetx");
  try {
    const r = await api("/v1/human-evidence", { method: "POST", body: { decision_id: decisionId, signal_type: signal, payload, referenced_run_ids: [runId], consent: { explicit_feedback: true } } });
    const p = r.proposal;
    st.innerHTML = `<div class="banner ok">Recorded as your evidence (${esc(r.interpretation.interpretation)}).</div>
      ${p ? `<p class="small">A ${esc(p.mode)} profile proposal was created and needs your approval: <a href="#/profile">review it</a>.</p>` : '<p class="small muted">No profile change implied.</p>'}`;
  } catch (e) { st.innerHTML = errorBox(e); }
}

function correctionSheet(decisionId, runId) {
  openSheet(`<h2>Correct how your Agent represents you</h2>
    <p class="small muted">This becomes a proposal on your real profile. Nothing changes until you approve it.</p>
    <form class="stack" id="cf">
      <label>What would you say?<input type="text" name="statement" placeholder="I will not accept terms that remove founder control"></label>
      <label>Feature key (optional)<input type="text" name="key" placeholder="constraint.founder_control_retained"></label>
      <label>Value (JSON)<input type="text" name="value" placeholder='{"op": "==", "value": true}'></label>
      <label><input type="checkbox" name="hard"> This is a hard boundary for me</label>
      <button class="btn primary" type="submit">Send correction</button><div id="sheetx"></div>
    </form>`);
  $("#cf").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    let value = f.get("value"); try { value = value ? JSON.parse(value) : null; } catch { /* keep as text */ }
    sendFeedback(decisionId, runId, "EXPLICIT_CORRECTION", { statement: f.get("statement"), feature_key: f.get("key") || null, value, hard: !!f.get("hard") });
  });
}

/* ---------------------------------------------------------------- compare */
async function viewCompare(a, b) {
  setNav("home");
  loading("Comparing");
  try {
    const c = await api(`/v1/runs/${a}/compare/${b}`);
    const [ra, rb] = await Promise.all([api(`/v1/runs/${a}`), api(`/v1/runs/${b}`)]);
    const side = (r, k) => `<div class="card"><div class="row between"><strong>${k}: ${esc(r.manifest.label)}</strong>${statusChip(r.result?.status)}</div>
      <p class="small">${esc(c.outcomes[k.toLowerCase()] || "")}</p>
      ${c.first_divergence[k.toLowerCase()] ? `<p class="small"><strong>First differing event</strong> (${esc(fmtTime(c.first_divergence[k.toLowerCase()].sim_at, true))}): ${esc(c.first_divergence[k.toLowerCase()].text)}</p>` : ""}
      <p><a href="#/r/${esc(r.manifest.run_id)}">Open timeline →</a></p></div>`;
    app.innerHTML = `<p class="small"><a href="#/d/${esc(ra.manifest.decision_id)}/overview">← Trajectories</a></p><h1>Divergence</h1>
      ${c.banner ? `<div class="banner warn">${esc(c.banner)}</div>` : '<div class="banner sim">Same model and exogenous random streams: differences are attributable to the intervention inside this model.</div>'}
      <div class="card"><h3>Intervention</h3>${Object.keys(c.intervention).length ? Object.entries(c.intervention).map(([k, v]) => `<p class="small"><strong>${esc(k)}</strong>: A=${esc(JSON.stringify(v.a))} · B=${esc(JSON.stringify(v.b))}</p>`).join("") : '<p class="small muted">No manifest difference.</p>'}
        <p class="small">Shared committed events before divergence: <strong>${c.common_prefix_events}</strong></p>
        <p class="small mono">ancestor state ${esc(c.common_ancestor_state_hash || "")}</p></div>
      <div class="grid2">${side(ra, "A")}${side(rb, "B")}</div>
      ${c.counterfactual ? `<div class="card small"><h3>Counterfactual</h3>${esc(c.counterfactual)}</div>` : ""}
      <h2>Metrics at end</h2><div class="card tablewrap"><table><tr><th>Metric</th><th>A</th><th>B</th></tr>${Object.entries(c.metrics).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${fmtVal(v.a?.value ?? v.a?.status)}</td><td>${fmtVal(v.b?.value ?? v.b?.status)}</td></tr>`).join("")}</table></div>
      <h2>Limitations</h2>${c.limitations.map((l) => `<p class="small">• ${esc(l)}</p>`).join("")}`;
  } catch (e) { app.innerHTML = errorBox(e); }
}

/* ---------------------------------------------------------------- profile */
async function viewProfile() {
  setNav("profile");
  loading("Loading your profile");
  try {
    const me = ME || (ME = await api("/v1/me"));
    if (!me.profile_ids.length) { app.innerHTML = '<div class="empty">No profile for this user.</div>'; return; }
    const p = await api(`/v1/profiles/${me.profile_ids[0]}`);
    const cur = p.current;
    app.innerHTML = `<h1>Your profile</h1>
      <p class="muted small">Private to you. Simulated Agent behaviour never changes it; only your own explicit evidence can, after your approval.</p>
      <h2>Features (v${cur.version})</h2>
      ${cur.features.map((f) => `<div class="card"><div class="row between"><strong>${esc(f.key)}</strong><span class="chip ${f.status === "HARD_CONSTRAINT" ? "bad" : f.status === "UNKNOWN" ? "warn" : ""}">${esc(f.status)}</span></div>
        <p class="small mono">${esc(JSON.stringify(f.value))}</p>
        <div class="row small muted"><span>confidence ${esc(f.epistemic_confidence)}</span><span>· relevance ${f.relevance ?? "—"}</span><span>· ${esc(f.privacy)}</span><span>· evidence ${esc(f.evidence_refs.join(", "))}</span></div>
        ${f.note ? `<p class="small">${esc(f.note)}</p>` : ""}</div>`).join("")}
      <h2>Proposals</h2>
      ${p.proposals.length ? p.proposals.map((x) => `<div class="card"><div class="row between"><strong class="small">${esc(x.reason)}</strong><span class="chip">${esc(x.status)}</span></div>
        ${x.changes.map((c) => `<p class="small">${esc(c.change_kind)} · <strong>${esc(c.key)}</strong>: ${esc(JSON.stringify(c.before?.value ?? null))} → ${esc(JSON.stringify(c.after?.value ?? null))}</p>`).join("")}
        ${x.mode === "shadow" ? '<p class="small muted">Shadow mode: recorded for calibration, never applied automatically.</p>' : ""}
        ${x.status === "PROPOSED" ? `<div class="row"><button class="btn small primary" data-ap="${esc(x.proposal_id)}">Approve</button><button class="btn small" data-rj="${esc(x.proposal_id)}">Reject</button></div>` : ""}</div>`).join("") : '<p class="muted small">No proposals.</p>'}
      <h2>Versions</h2>${p.versions.map((v) => `<div class="card row between"><span class="small">v${v.version} · ${esc(v.reason)}</span>${v.version !== cur.version ? `<button class="btn small" data-rb="${v.version}">Roll back</button>` : '<span class="chip ok">current</span>'}</div>`).join("")}`;
    const act = async (body, path = "approvals") => { await api(`/v1/profiles/${cur.profile_id}/${path}`, { method: "POST", body }); viewProfile(); };
    app.querySelectorAll("[data-ap]").forEach((b) => b.addEventListener("click", () => act({ proposal_id: b.dataset.ap, approve: true })));
    app.querySelectorAll("[data-rj]").forEach((b) => b.addEventListener("click", () => act({ proposal_id: b.dataset.rj, approve: false })));
    app.querySelectorAll("[data-rb]").forEach((b) => b.addEventListener("click", () => act({ to_version: Number(b.dataset.rb) }, "rollback")));
  } catch (e) { app.innerHTML = errorBox(e); }
}

async function viewAudit() {
  setNav("audit");
  loading("Loading boundary log");
  try {
    const rows = await api("/v1/audit?denied_only=true");
    app.innerHTML = `<h1>Epistemic boundary log</h1>
      <p class="muted small">Every refused write across Layer A (shared knowledge), Layer B (models), Layer C (simulation) and the real profile store.</p>
      ${rows.length ? `<div class="card tablewrap"><table><tr><th>When</th><th>Who</th><th>Operation</th><th>Reason</th></tr>${rows.map((r) => `<tr><td class="small">${esc(r.at.slice(0, 19))}</td><td class="small">${esc(r.role)}</td><td class="small">${esc(r.operation)}<br><span class="muted">${esc(r.target)}</span></td><td class="small">${esc(r.reason)}</td></tr>`).join("")}</table></div>` : '<div class="empty">No refused writes yet. Try “Try to promote as real evidence” on any simulated event.</div>'}`;
  } catch (e) { app.innerHTML = errorBox(e); }
}

/* ---------------------------------------------------------------- router */
async function route() {
  closeSheet();
  const h = location.hash.replace(/^#\/?/, "");
  const parts = h.split("/").filter(Boolean);
  if (!TOKEN && parts[0] !== "login") { location.hash = "#/login"; return; }
  if (TOKEN && !ME && parts[0] !== "login") {
    try { ME = await api("/v1/me"); } catch { TOKEN = null; store.del("udse_token"); location.hash = "#/login"; return; }
  }
  $("#who").textContent = ME ? ME.display_name : "Sign in";
  app.focus({ preventScroll: true });
  window.scrollTo(0, 0);
  if (parts[0] === "login") return viewLogin();
  if (parts[0] === "d") return viewDecision(parts[1], parts[2] || "overview");
  if (parts[0] === "r") return viewRun(parts[1]);
  if (parts[0] === "c") return viewCompare(parts[1], parts[2]);
  if (parts[0] === "profile") return viewProfile();
  if (parts[0] === "audit") return viewAudit();
  return viewHome();
}
window.addEventListener("hashchange", route);
route();
