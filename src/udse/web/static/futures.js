/* Futures map: branching timeline of simulated paths (docs/design/ui-redesign.md §3, §5.3–5.5).
 * Layout is computed in abstract (time, cross) space and projected horizontally (time →) or
 * vertically (time ↓) so one renderer serves desktop and phones. */

import {
  $, $$, api, closeSheet, esc, fmtNum, fmtTime, humanize, initials, ms, openSheet, outcomeColor, personColor,
  reducedMotion, simStamp, tierMeter, tierWords, toast, TIERS,
} from "./util.js";

const TIER_STYLE = {
  A_GROUNDED: { dash: "", op: 1 },
  B_ASSUMPTION_BOUND: { dash: "", op: 0.82 },
  C_ILLUSTRATIVE: { dash: "8 5", op: 0.9 },
  X_LIMITED_OR_ABSTAIN: { dash: "1.5 6", op: 0.9 },
};
const WIDTH = { detailed: 2.8, preview: 1.7, probe: 1.3 };
const KIND_WORD = { action: "Agent action", agreement: "Agreement", threshold: "Threshold crossed", external: "From outside", info: "Information", blocked: "Blocked by a rule", end: "End", start: "Start" };

function tzOffsetMin(iso) {
  const m = /([+-])(\d\d):(\d\d)$/.exec(iso || "");
  return m ? (m[1] === "-" ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
}

/* ------------------------------------------------------------------ ticks */
function makeTicks(h, axisPx) {
  const s = ms(h.start), e = ms(h.end), off = tzOffsetMin(h.start) * 60000, target = Math.max(3, Math.floor(axisPx / 105));
  const out = [];
  if (h.resolution === "minute" || h.resolution === "hour") {
    const step = [15, 30, 60, 120, 180, 360, 720].map((m) => m * 60000).find((st) => (e - s) / st <= target) || 720 * 60000;
    for (let t = Math.ceil((s + off) / step) * step - off; t <= e; t += step) {
      const d = new Date(t + off);
      out.push({ t, label: `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}` });
    }
    return out;
  }
  if (h.resolution === "day" || h.resolution === "week") {
    const step = [1, 2, 7, 14, 28].map((d) => d * 864e5).find((st) => (e - s) / st <= target) || 28 * 864e5;
    for (let t = Math.ceil((s + off) / 864e5) * 864e5 - off; t <= e; t += step) {
      const d = new Date(t + off);
      out.push({ t, label: `${d.getUTCDate()} ${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getUTCMonth()]}` });
    }
    return out;
  }
  const months = (e - s) / (30.44 * 864e5);
  const stepM = [1, 2, 3, 6, 12].find((m) => months / m <= target) || 12;
  const sd = new Date(s + off);
  let y = sd.getUTCFullYear(), mo = sd.getUTCMonth() + 1;
  mo = Math.ceil(mo / stepM) * stepM;
  for (let i = 0; i < 200; i++) {
    const yy = y + Math.floor(mo / 12), mm = mo % 12;
    const t = Date.UTC(yy, mm, 1) - off;
    if (t > e) break;
    if (t >= s) out.push({ t, label: (mm === 0 || out.length === 0) ? `${["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][mm]} ${yy}` : ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][mm] });
    mo += stepM;
  }
  return out;
}

/* ------------------------------------------------------------------ model */
function visibleThreads(data, st) {
  const byId = Object.fromEntries(data.threads.map((t) => [t.thread_id, t]));
  const trunk = data.threads[0];
  if (st.compare) {
    const a = byId[st.compare.a], b = byId[st.compare.b];
    const cmp = st.compare.cmp;
    const split = cmp && cmp.first_divergence && cmp.first_divergence.b ? cmp.first_divergence.b.sim_at : (cmp && cmp.first_divergence && cmp.first_divergence.a ? cmp.first_divergence.a.sim_at : b.split_at);
    return [{ ...a, parent: null, split_at: data.horizon.start, split_index: 0, _lane: 0 },
      { ...b, parent: a.thread_id, split_at: split, split_index: cmp ? cmp.common_prefix_events : b.split_index }];
  }
  const keep = (t) => t === trunk || t.detail === "detailed" || (t.detail === "preview" && st.showPreviews) || (t.detail === "probe" && st.showProbes);
  const vis = data.threads.filter(keep);
  const visSet = new Set(vis.map((t) => t.thread_id));
  return vis.map((t) => {
    if (t === trunk) return { ...t, parent: null };
    let p = t.parent, split = t.split_at, idx = t.split_index;
    while (p && !visSet.has(p)) {
      const pt = byId[p];
      if (ms(pt.split_at) < ms(split)) { split = pt.split_at; idx = Math.min(idx, pt.split_index); }
      p = pt.parent;
    }
    return { ...t, parent: p || trunk.thread_id, split_at: split, split_index: idx };
  });
}

function laneOrder(threads) {
  const kids = {};
  threads.forEach((t) => { if (t.parent) (kids[t.parent] = kids[t.parent] || []).push(t); });
  const order = (node) => {
    const ks = (kids[node.thread_id] || []).slice().sort((a, b) => ms(b.split_at) - ms(a.split_at));
    const above = [], below = [];
    ks.forEach((k, i) => (i % 2 === 0 ? below : above).push(k));
    return [...above.reverse().flatMap(order), node, ...below.flatMap(order)];
  };
  const root = threads.find((t) => !t.parent);
  return order(root);
}

const valueAt = (series, t) => {
  if (!series || !series.length) return null;
  let v = series[0][1];
  for (const [ti, vi] of series) { if (ms(ti) <= t) v = vi; else break; }
  return v;
};
const smooth = (x) => x * x * (3 - 2 * x);

/* Split a time-ordered polyline at time b (interpolating). */
function splitAt(pts, b) {
  const left = [], right = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (p[0] <= b) left.push(p);
    if (p[0] >= b) right.push(p);
    const q = pts[i + 1];
    if (q && p[0] < b && q[0] > b) {
      const f = (b - p[0]) / (q[0] - p[0]);
      const m = [b, p[1] + f * (q[1] - p[1])];
      left.push(m); right.push(m);
    }
  }
  return [left, right];
}

/* ------------------------------------------------------------------ render */
export function mountFutures(root, ctx) {
  const data = ctx.data;
  const narrow = () => root.clientWidth < 640;
  const st = {
    lens: "structure", showPreviews: !narrow(), showProbes: false, showGhosts: true,
    focus: (ctx.focusRun && (data.threads.find((t) => t.run_ids.includes(ctx.focusRun)) || {}).thread_id) || null, compare: null, picking: false, picked: [], cursor: null, animated: false,
  };
  const famIndex = Object.fromEntries(data.families.map((f) => [f.key, f.color_index]));
  const peopleIdx = Object.fromEntries(data.people.map((p, i) => [p.id, i]));
  const peopleName = Object.fromEntries(data.people.map((p) => [p.id, p.name]));
  const keyNo = Object.fromEntries(data.threads.map((t, i) => [t.thread_id, i + 1]));
  const colorOf = (t) => outcomeColor(famIndex[t.family] ?? 0);
  const res = data.horizon.resolution;
  let geo = null;

  function render() {
    const hasStory = !!(st.focus || st.compare);
    root.innerHTML = `
      <div class="futures ${hasStory ? "has-story" : ""}">
        <div class="stack">
          <div class="card mapcard">
            <div class="mapbar">
              <div class="row">
                <span class="eyebrow">Lens</span>
                <div class="seg" role="group" aria-label="Map lens">
                  <button data-lens="structure" aria-pressed="${st.lens === "structure"}">Shape of futures</button>
                  ${data.metrics.map((m) => `<button data-lens="${esc(m.id)}" aria-pressed="${st.lens === m.id}">${esc(m.label)}</button>`).join("")}
                </div>
              </div>
              <div class="row">
                <label class="toggle"><input type="checkbox" data-t="showPreviews" ${st.showPreviews ? "checked" : ""}> Previews</label>
                <label class="toggle"><input type="checkbox" data-t="showProbes" ${st.showProbes ? "checked" : ""}> Sensitivity probes</label>
                <label class="toggle"><input type="checkbox" data-t="showGhosts" ${st.showGhosts ? "checked" : ""}> Unexplored</label>
                ${st.compare ? '<button class="btn small" data-act="endcompare">Exit compare</button>'
                  : `<button class="btn small ${st.picking ? "primary" : ""}" data-act="compare">${st.picking ? `Pick ${2 - st.picked.length} path${st.picked.length === 1 ? "" : "s"}…` : "Compare two paths"}</button>`}
              </div>
            </div>
            <div class="mapwrap ${narrow() ? "vertical" : ""}">
              ${knownGutter()}
              <div class="svgbox" tabindex="0" aria-label="Futures map. Use left and right arrow keys to move the time cursor.">
                <svg class="map" id="map" role="img"></svg>
                <div class="readout" id="readout" hidden></div>
              </div>
            </div>
            ${legendBar()}
          </div>
          ${st.compare ? "" : `<div class="card"><div class="row between"><h3 style="margin:0">Paths</h3><span class="tiny muted">${data.threads.length} distinct simulated paths · counts are coverage, never odds</span></div>
            <div class="threads" style="margin-top:8px">${threadList()}</div></div>`}
          ${ghostList()}
        </div>
        ${hasStory ? `<aside class="card story" id="story" aria-live="polite"></aside>` : ""}
      </div>`;
    bindControls();
    draw();
    if (st.compare) renderCompare(); else if (st.focus) renderStory();
  }

  function knownGutter() {
    const ev = data.evidence;
    const gaps = ev.gaps.filter((g) => g.kind !== "FORECAST_INPUT").slice(0, 2);
    return `<aside class="known" aria-label="What is known now">
      <span class="eyebrow">Known now</span>
      <div class="kcounts">
        <span class="kc"><span class="kglyph">✓</span><b>${ev.verified}</b> verified</span>
        <span class="kc"><span class="kglyph">✎</span><b>${ev.user}</b> you said</span>
        <span class="kc"><span class="kglyph">⌛</span><b>${ev.stale}</b> stale</span>
        <span class="kc"><span class="kglyph">?</span><b>${ev.unknown}</b> unknown</span>
        ${ev.contested ? `<span class="kc"><span class="kglyph">⚠</span><b>${ev.contested}</b> contested</span>` : ""}
      </div>
      ${gaps.map((g) => `<div class="gap">${esc(g.question.replace(/^Find: /, ""))} <span class="muted">— unknown</span></div>`).join("")}
      ${ev.warnings.length ? `<div class="gap" style="border-color:var(--bad)">A source contained instructions — treated as data only</div>` : ""}
      <a class="small" href="#/w/${esc(ctx.decisionId)}/evidence" style="color:var(--ground)">Open evidence →</a>
    </aside>`;
  }

  function legendBar() {
    const g = (svg) => `<svg width="26" height="14" viewBox="0 0 26 14" aria-hidden="true">${svg}</svg>`;
    return `<div class="legendbar" aria-label="How to read the map">
      <span>${g('<line x1="2" y1="7" x2="24" y2="7" stroke="var(--text-2)" stroke-width="2.4"/>')} grounded / stated assumptions</span>
      <span>${g('<line x1="2" y1="7" x2="24" y2="7" stroke="var(--text-2)" stroke-width="2.4" stroke-dasharray="7 4"/>')} illustrative (frayed)</span>
      <span>${g('<circle cx="13" cy="7" r="4.5" fill="var(--bg)" stroke="var(--present)" stroke-width="2"/>')} paths split</span>
      <span>${g('<circle cx="13" cy="7" r="4.5" fill="none" stroke="var(--text-2)" stroke-width="2"/>')} reached horizon</span>
      <span>${g('<rect x="8.5" y="2.5" width="9" height="9" fill="var(--text-2)"/>')} ended</span>
      <span>${g('<circle cx="6" cy="7" r="1.6" fill="var(--muted)"/><circle cx="13" cy="7" r="1.6" fill="var(--muted)"/><circle cx="20" cy="7" r="1.6" fill="var(--muted)"/>')} stopped early</span>
      <span>${g('<line x1="2" y1="7" x2="24" y2="7" stroke="var(--ghost)" stroke-width="1.6" stroke-dasharray="2 5"/>')} identified, not simulated</span>
      <span class="muted">Left of the amber line: real evidence. Right of it: simulation.</span>
    </div>`;
  }

  function threadList() {
    return data.threads.map((t) => {
      const hidden = !(t.detail === "detailed" || (t.detail === "preview" && st.showPreviews) || (t.detail === "probe" && st.showProbes) || t === data.threads[0]);
      const pickedIdx = st.picked.indexOf(t.thread_id);
      return `<button class="tl" data-thread="${t.thread_id}" aria-pressed="${st.focus === t.thread_id || pickedIdx >= 0}" ${hidden ? 'style="opacity:.55"' : ""}>
        <span class="k" style="background:${colorOf(t)}">${keyNo[t.thread_id]}</span>
        <span style="min-width:0"><span class="tt">${esc(t.label)}</span>
          <span class="ts">${esc(t.detail === "detailed" ? "Detailed simulation" : t.detail === "preview" ? "Exploratory preview" : "Sensitivity probe")}${t.also.length ? " · also " + esc(t.also.join(", ")) : ""}${t.carries.length ? " · " + esc(t.carries.join(" · ")) : ""}</span></span>
        <span class="te" style="color:${colorOf(t)}">${esc(t.outcome_label)}<br><span class="muted tiny">${esc(fmtTime(t.ended_at, res, true))}</span></span>
      </button>`;
    }).join("");
  }

  function ghostList() {
    if (!data.ghosts.length) return "";
    const glyph = { ruled_out: "✂", set_aside: "⏸", reopened: "↺", unsimulated: "┄" };
    return `<div class="card"><div class="row between"><h3 style="margin:0">Identified, not simulated</h3>
        <a class="small" href="#/w/${esc(ctx.decisionId)}/possibilities">All ${data.ghosts_total} possibilities →</a></div>
      <p class="tiny muted">Coverage is not exhaustive. These were identified but not simulated — and nobody can list what was never identified.</p>
      <div class="chips" style="margin-top:8px">${data.ghosts.map((g, i) => `<button class="pill" data-ghost="${i}" style="cursor:pointer;${g.kind === "ruled_out" ? "color:var(--cut)" : ""}">${glyph[g.kind] || "┄"} ${esc(g.label)}</button>`).join("")}</div></div>`;
  }

  /* ---------------- geometry + svg ---------------- */
  function draw() {
    const svg = $("#map", root);
    const box = $(".svgbox", root);
    const W = Math.max(300, box.clientWidth);
    const vertical = narrow();
    const threads = visibleThreads(data, st);
    const order = laneOrder(threads);
    const lane = Object.fromEntries(order.map((t, i) => [t.thread_id, i]));
    const t0 = ms(data.horizon.start), t1 = ms(data.horizon.end), span = t1 - t0;
    const metric = st.lens !== "structure" ? data.metrics.find((m) => m.id === st.lens) : null;
    const showGhosts = st.showGhosts && !vertical && !st.compare && !metric;
    const ghosts = showGhosts ? data.ghosts.slice(0, 6) : [];
    const gAbove = Math.ceil(ghosts.length / 2), gBelow = Math.floor(ghosts.length / 2);
    const wideLabels = !vertical && W > 820;
    const mL = vertical ? 54 : 20, mR = vertical ? 18 : (wideLabels ? 236 : 44), mT = vertical ? 38 : 26, mB = vertical ? 40 : 34;
    const laneGap = vertical ? Math.max(24, Math.min(46, (W - mL - mR) / Math.max(order.length, 1)))
      : Math.min(72, Math.max(order.length > 10 ? 30 : 38, 200 / Math.max(order.length, 1)));
    const ghostGap = 26;
    let H, axisLen, crossLen;
    if (vertical) {
      axisLen = Math.max(560, Math.min(980, order.length * 60 + 420));
      H = mT + axisLen + mB;
      crossLen = W - mL - mR;
    } else {
      crossLen = metric ? Math.max(300, order.length * 30) : order.length * laneGap + (gAbove + gBelow) * ghostGap;
      H = mT + crossLen + mB;
      axisLen = W - mL - mR;
    }
    const tPx = (t) => ((Math.min(Math.max(t, t0), t1) - t0) / span) * axisLen;
    // cross coordinate for a lane (structure) or a value (metric)
    let vMin = 0, vMax = 1, logScale = false;
    const sl = (v) => Math.sign(v) * Math.log10(1 + Math.abs(v));
    const isl = (u) => Math.sign(u) * (Math.pow(10, Math.abs(u)) - 1);
    if (metric) {
      const vals = threads.flatMap((t) => (t.series[metric.id] || []).map((p) => p[1]));
      const pos = vals.filter((v) => v > 0).sort((a, b) => a - b);
      const med = pos.length ? pos[Math.floor(pos.length / 2)] : 0;
      logScale = med > 0 && Math.max(...vals) / med > 15 && Math.min(...vals) >= 0;
      const tf = logScale ? sl : (v) => v;
      vMin = Math.min(0, ...vals.map(tf)); vMax = Math.max(...vals.map(tf), vMin + 1e-9);
      const pad = (vMax - vMin) * 0.06; vMax += pad; if (vMin < 0) vMin -= pad;
    }
    const tfm = logScale ? sl : (v) => v;
    const laneX = (l) => (vertical ? (l + 0.5) * laneGap + (crossLen - order.length * laneGap) / 2 : gAbove * ghostGap + (l + 0.5) * laneGap);
    const valX = (raw) => { const v = tfm(raw); return vertical ? ((v - vMin) / (vMax - vMin)) * crossLen : crossLen - ((v - vMin) / (vMax - vMin)) * crossLen; };
    const P = (t, c) => (vertical ? [mL + c, mT + tPx(t)] : [mL + tPx(t), mT + c]);
    const curveT = Math.min(span * 0.05, span * (60 / axisLen));
    geo = { P, tPx, t0, t1, span, mL, mT, vertical, W, H, axisLen, crossLen, threads, lane, laneX, valX, metric };

    // polyline (time, cross) per thread
    const lines = {};
    for (const t of threads) {
      const own = laneX(lane[t.thread_id]);
      const tS = t.parent ? ms(t.split_at) : t0;
      const tE = Math.max(ms(t.ended_at), tS);
      let pts = [];
      if (metric) {
        const s = t.series[metric.id] || [];
        let v = valueAt(s, tS);
        if (v === null) { lines[t.thread_id] = null; continue; }
        pts.push([tS, valX(v)]);
        for (const [ti, vi] of s) {
          const tm = ms(ti);
          if (tm <= tS) continue;
          if (tm > tE) break;
          pts.push([tm, valX(v)]); pts.push([tm, valX(vi)]); v = vi;
        }
        pts.push([tE, valX(v)]);
      } else if (!t.parent) {
        pts = [[t0, own], [tE, own]];
      } else {
        const from = laneX(lane[t.parent]);
        const len = Math.max(curveT, 1);
        for (let i = 0; i <= 14; i++) {
          const f = i / 14;
          pts.push([tS + f * len, from + (own - from) * smooth(f)]);
        }
        if (tE > tS + len) pts.push([tE, own]);
      }
      lines[t.thread_id] = pts;
    }
    geo.lines = lines;
    const crossAt = (t, time) => {
      const pts = lines[t.thread_id];
      if (!pts) return null;
      let c = pts[0][1];
      for (let i = 0; i < pts.length; i++) {
        if (pts[i][0] <= time) c = pts[i][1];
        const q = pts[i + 1];
        if (q && pts[i][0] < time && q[0] > time) return pts[i][1] + ((time - pts[i][0]) / (q[0] - pts[i][0])) * (q[1] - pts[i][1]);
      }
      return c;
    };
    geo.crossAt = crossAt;

    const focusOn = st.focus && !st.compare;
    const dim = (id) => (focusOn && st.focus !== id ? " dim" : "");
    const parts = [];
    parts.push(`<defs>
      <clipPath id="reveal"><rect id="revealRect" x="0" y="0" width="${vertical ? W : (st.animated || reducedMotion() ? W : 0)}" height="${vertical ? (st.animated || reducedMotion() ? H : 0) : H}"/></clipPath>
      <linearGradient id="fogT" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--bg)" stop-opacity=".9"/><stop offset="1" stop-color="var(--bg)" stop-opacity="0"/></linearGradient>
      <linearGradient id="fogB" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="var(--bg)" stop-opacity=".9"/><stop offset="1" stop-color="var(--bg)" stop-opacity="0"/></linearGradient>
    </defs>`);
    // ticks and axis
    for (const tk of makeTicks(data.horizon, axisLen)) {
      const [x, y] = P(tk.t, 0);
      if (vertical) {
        parts.push(`<line class="tick" x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}"/><text x="${mL - 8}" y="${y + 4}" text-anchor="end">${esc(tk.label)}</text>`);
      } else {
        parts.push(`<line class="tick" x1="${x}" y1="${mT}" x2="${x}" y2="${mT + crossLen}"/><text x="${x}" y="${H - 12}" text-anchor="middle">${esc(tk.label)}</text>`);
      }
    }
    if (metric && logScale) parts.push(`<text x="${vertical ? W - mR : mL + axisLen + 6}" y="${mT - 12}" text-anchor="${vertical ? "end" : "start"}" font-style="italic">log scale</text>`);
    if (metric) {
      for (const f of [0, 0.25, 0.5, 0.75, 1]) {
        const u = vMin + f * (vMax - vMin);
        const v = logScale ? isl(u) : u;
        const c = valX(v);
        if (vertical) parts.push(`<text x="${mL + c}" y="${mT - 14}" text-anchor="middle">${esc(fmtNum(v, metric.unit))}</text>`);
        else parts.push(`<line class="gridv" x1="${mL}" y1="${mT + c}" x2="${mL + axisLen}" y2="${mT + c}"/><text x="${mL + axisLen + 6}" y="${mT + c + 4}">${esc(fmtNum(v, metric.unit))}</text>`);
      }
    }
    // present + horizon
    const [px0, py0] = P(t0, 0), [px1, py1] = P(t1, 0);
    if (vertical) {
      parts.push(`<line class="horizon" x1="${mL - 4}" y1="${py1}" x2="${W - mR}" y2="${py1}"/><text x="${W - mR}" y="${py1 + 16}" text-anchor="end">horizon ${esc(fmtTime(data.horizon.end, res, true))}</text>`);
      parts.push(`<line class="present" x1="${mL - 6}" y1="${py0}" x2="${W - mR}" y2="${py0}"/><text class="present-label" x="${W - mR}" y="${py0 - 8}" text-anchor="end">NOW · SIMULATION STARTS</text>`);
    } else {
      parts.push(`<line class="horizon" x1="${px1}" y1="${mT - 10}" x2="${px1}" y2="${mT + crossLen}"/><text x="${px1}" y="${mT - 12}" text-anchor="middle">horizon</text>`);
      parts.push(`<line class="present" x1="${px0}" y1="${mT - 14}" x2="${px0}" y2="${mT + crossLen + 4}"/><text class="present-label" x="${px0 + 6}" y="${mT - 12}">NOW · SIMULATION STARTS</text>`);
    }
    // ghosts (identified, not simulated) fanning from the present
    if (ghosts.length) {
      const trunk = threads.find((t) => !t.parent);
      const from = laneX(lane[trunk.thread_id]);
      ghosts.forEach((g, i) => {
        const above = i % 2 === 0, k = Math.floor(i / 2);
        const c = above ? (gAbove - 1 - k + 0.5) * ghostGap : gAbove * ghostGap + order.length * laneGap + (k + 0.5) * ghostGap;
        const len = span * (g.kind === "ruled_out" ? 0.07 : 0.11);
        const pts = [];
        for (let j = 0; j <= 12; j++) { const f = j / 12; pts.push(P(t0 + f * len, from + (c - from) * smooth(f))); }
        const [ex, ey] = pts[pts.length - 1];
        const cut = g.kind === "ruled_out" ? `<line class="cut" x1="${ex - 4}" y1="${ey - 6}" x2="${ex + 4}" y2="${ey + 6}"/><line class="cut" x1="${ex + 4}" y1="${ey - 6}" x2="${ex - 4}" y2="${ey + 6}"/>` : "";
        parts.push(`<g class="ghost-g" data-ghost="${i}" tabindex="0" role="button" aria-label="${esc(g.label)}: ${esc(g.ui_label)}">
          <path class="ghost" d="M${pts.map((p) => p.join(",")).join(" L")}"/>${cut}
          <text class="ghost-label" x="${ex + 10}" y="${ey + 4}">${esc(g.label)} <tspan opacity=".7">— ${esc(g.kind === "ruled_out" ? "ruled out" : g.kind === "set_aside" ? "set aside" : g.why.length > 34 ? g.why.slice(0, 34) + "…" : g.why)}</tspan></text>
          <path class="hit" d="M${pts.map((p) => p.join(",")).join(" L")}"/></g>`);
      });
      if (gAbove) parts.push(`<rect class="fog" x="${mL}" y="${mT - 2}" width="${axisLen}" height="${gAbove * ghostGap * 0.55}" fill="url(#fogT)" opacity=".35"/>`);
    }
    // threads (tier-styled segments)
    parts.push(`<g clip-path="url(#reveal)">`);
    const drawOrder = [...threads].sort((a, b) => (a.thread_id === st.focus) - (b.thread_id === st.focus) || (a.detail === "detailed") - (b.detail === "detailed"));
    for (const t of drawOrder) {
      const pts = lines[t.thread_id];
      if (!pts) continue;
      const tS = pts[0][0];
      const breaks = (t.tier_path || []).map(([ti, tier]) => [ms(ti), tier]);
      let segs = [], rest = pts, cur = breaks.filter(([b]) => b <= tS).pop()?.[1] || (breaks[0] ? breaks[0][1] : "B_ASSUMPTION_BOUND");
      for (const [b, tier] of breaks) {
        if (b <= tS) continue;
        const [l, r] = splitAt(rest, b);
        if (l.length > 1) segs.push([l, cur]);
        rest = r; cur = tier;
      }
      if (rest.length > 1) segs.push([rest, cur]);
      const color = colorOf(t), w = WIDTH[t.detail] || 1.6;
      for (const [seg, tier] of segs) {
        const s = TIER_STYLE[tier] || TIER_STYLE.B_ASSUMPTION_BOUND;
        const d = "M" + seg.map(([tt, c]) => P(tt, c).map((v) => v.toFixed(1)).join(",")).join(" L");
        parts.push(`<path class="thread${dim(t.thread_id)}" d="${d}" stroke="${color}" stroke-width="${st.focus === t.thread_id ? w + 0.8 : w}" stroke-opacity="${s.op}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/>`);
      }
      const dAll = "M" + pts.map(([tt, c]) => P(tt, c).map((v) => v.toFixed(1)).join(",")).join(" L");
      parts.push(`<path class="hit" data-thread="${t.thread_id}" d="${dAll}"><title>${esc(t.label)} — ${esc(t.outcome_label)}</title></path>`);
    }
    // beads
    for (const t of drawOrder) {
      if (!lines[t.thread_id]) continue;
      const big = st.focus === t.thread_id || st.compare;
      for (const m of t.moments) {
        if (m.kind === "start" || m.kind === "end") continue;
        if (t.parent && m.i < t.split_index) continue;
        const tm = ms(m.t), c = crossAt(t, tm);
        if (c === null) continue;
        const [x, y] = P(tm, c);
        const r = big ? 5 : 3.6;
        const col = colorOf(t);
        let shape;
        if (m.kind === "action") shape = `<circle cx="${x}" cy="${y}" r="${r}" fill="var(--bg)" stroke="${personColor(peopleIdx[m.actor] ?? 0)}" stroke-width="${big ? 2.4 : 1.8}"/>`;
        else if (m.kind === "agreement") shape = `<rect x="${x - r}" y="${y - r}" width="${2 * r}" height="${2 * r}" transform="rotate(45 ${x} ${y})" fill="${col}" stroke="var(--bg)" stroke-width="1.5"/>`;
        else if (m.kind === "threshold") shape = `<path d="M${x},${y - r - 1} L${x + r + 1},${y + r} L${x - r - 1},${y + r} Z" fill="var(--warn)" stroke="var(--bg)" stroke-width="1.2"/>`;
        else if (m.kind === "external") shape = `<path d="M${x},${y - r - 2} L${x + 1.6},${y - 1.6} L${x + r + 2},${y} L${x + 1.6},${y + 1.6} L${x},${y + r + 2} L${x - 1.6},${y + 1.6} L${x - r - 2},${y} L${x - 1.6},${y - 1.6} Z" fill="var(--present)"/>`;
        else if (m.kind === "blocked") shape = `<path d="M${x - r},${y - r} L${x + r},${y + r} M${x + r},${y - r} L${x - r},${y + r}" stroke="var(--cut)" stroke-width="2.2"/>`;
        else shape = `<circle cx="${x}" cy="${y}" r="${r - 0.8}" fill="var(--bg)" stroke="var(--muted)" stroke-width="1.4"/>`;
        parts.push(`<g class="bead${dim(t.thread_id)}" data-bead="${t.thread_id}:${m.i}">${shape}<title>${esc(fmtTime(m.t, res, true))} — ${esc(m.headline)}</title></g>`);
      }
    }
    parts.push(`</g>`);
    // nexus nodes (one per split position)
    const nex = {};
    for (const t of threads) {
      if (!t.parent || !lines[t.thread_id]) continue;
      const parent = threads.find((x) => x.thread_id === t.parent);
      const c = metric ? crossAt(parent, ms(t.split_at)) : laneX(lane[t.parent]);
      if (c === null) continue;
      const [x, y] = P(ms(t.split_at), c);
      const k = `${Math.round(x / 4)}:${Math.round(y / 4)}`;
      (nex[k] = nex[k] || { x, y, kids: [] }).kids.push(t);
    }
    for (const [k, n] of Object.entries(nex)) {
      const pulse = st.compare || (st.focus && n.kids.some((t) => t.thread_id === st.focus));
      parts.push(`<circle class="nexus ${pulse ? "pulse" : ""}" data-nexus="${esc(k)}" cx="${n.x}" cy="${n.y}" r="${n.kids.length > 1 ? 6 : 5}" tabindex="0" role="button" aria-label="Paths split here"><title>${n.kids.length} path${n.kids.length > 1 ? "s" : ""} split here — tap for what differed</title></circle>`);
    }
    geo.nexus = nex;
    // ends + keys + labels
    const labelSlots = [];
    for (const t of threads) {
      const pts = lines[t.thread_id];
      if (!pts) continue;
      const [tt, c] = pts[pts.length - 1];
      const [x, y] = P(tt, c);
      const col = colorOf(t);
      let g;
      if (t.horizon_reached) g = `<circle cx="${x}" cy="${y}" r="5.5" fill="var(--bg)" stroke="${col}" stroke-width="2.4"/>`;
      else if (t.stopped_early) g = [1, 2, 3].map((i) => { const [dx, dy] = vertical ? [0, i * 6] : [i * 6, 0]; return `<circle cx="${x + dx}" cy="${y + dy}" r="1.7" fill="${col}" opacity="${1 - i * 0.25}"/>`; }).join("");
      else g = `<rect x="${x - 5}" y="${y - 5}" width="10" height="10" rx="1.5" fill="${col}"/>`;
      const [kx, ky] = vertical ? [x, y + 20] : [x + 18, y];
      const key = `<g class="key${dim(t.thread_id)}" data-thread="${t.thread_id}" style="cursor:pointer"><circle class="key-bg" cx="${kx}" cy="${ky}" r="9" fill="${col}"/><text class="key-txt" x="${kx}" y="${ky + 3.5}" text-anchor="middle">${keyNo[t.thread_id]}</text></g>`;
      let label = "";
      if (wideLabels && !metric) {
        label = `<text class="end-label${dim(t.thread_id)}" x="${kx + 14}" y="${ky + 4}">${esc(t.outcome_label.length > 24 ? t.outcome_label.slice(0, 23) + "…" : t.outcome_label)}${t.horizon_reached ? "" : `<tspan fill="var(--muted)"> · ${esc(fmtTime(t.ended_at, res))}</tspan>`}</text>`;
      }
      labelSlots.push(`<g class="endg${dim(t.thread_id)}">${g}</g>${key}${label}`);
    }
    parts.push(labelSlots.join(""));
    // cursor
    parts.push(`<g id="cursorG" style="display:${st.cursor ? "inline" : "none"}"><line class="cursor" id="cursorLine"/></g>`);
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("height", H);
    svg.setAttribute("aria-label", summaryText(threads));
    svg.innerHTML = parts.join("");
    bindMap(svg);
    if (!st.animated && !reducedMotion()) {
      st.animated = true;
      const rect = $("#revealRect", svg), t0a = performance.now();
      const step = (now) => {
        // rAF timestamps can precede t0a on the first frame
        const f = Math.min(1, Math.max(0, (now - t0a) / 750)), e = 1 - Math.pow(1 - f, 3);
        if (vertical) rect.setAttribute("height", H * e); else rect.setAttribute("width", W * e);
        if (f < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    } else st.animated = true;
    if (st.cursor) placeCursor(st.cursor);
  }

  function summaryText(threads) {
    return `Futures map: ${threads.length} simulated paths from ${fmtTime(data.horizon.start, res, true)} to ${fmtTime(data.horizon.end, res, true)}. ` +
      threads.map((t) => `Path ${keyNo[t.thread_id]}, ${t.label}: ${t.outcome_label}`).join(". ");
  }

  /* ---------------- interaction ---------------- */
  function bindControls() {
    $$("[data-lens]", root).forEach((b) => b.addEventListener("click", () => { st.lens = b.dataset.lens; render(); }));
    $$("[data-t]", root).forEach((c) => c.addEventListener("change", () => { st[c.dataset.t] = c.checked; render(); }));
    $$(".tl", root).forEach((b) => b.addEventListener("click", () => pickOrFocus(b.dataset.thread)));
    $$("[data-ghost]", root).forEach((b) => b.addEventListener("click", () => ghostSheet(data.ghosts[Number(b.dataset.ghost)])));
    const cmpBtn = $('[data-act="compare"]', root);
    if (cmpBtn) cmpBtn.addEventListener("click", () => { st.picking = !st.picking; st.picked = []; if (st.picking) toast("Tap two paths to compare them"); render(); });
    const endBtn = $('[data-act="endcompare"]', root);
    if (endBtn) endBtn.addEventListener("click", () => { st.compare = null; st.picked = []; render(); });
    const box = $(".svgbox", root);
    box.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const cur = st.cursor ?? ms(data.horizon.start);
        moveCursor(Math.min(geo.t1, Math.max(geo.t0, cur + (e.key === "ArrowRight" ? 1 : -1) * geo.span / 60)));
      } else if (e.key === "Escape") { st.focus = null; st.cursor = null; render(); }
    });
  }

  async function pickOrFocus(id) {
    if (st.picking) {
      if (!st.picked.includes(id)) st.picked.push(id);
      if (st.picked.length === 2) {
        const [a, b] = st.picked.map((x) => data.threads.find((t) => t.thread_id === x));
        st.picking = false;
        try {
          const cmp = await api(`/v1/runs/${a.rep_run_id}/compare/${b.rep_run_id}`);
          st.compare = { a: a.thread_id, b: b.thread_id, cmp };
        } catch (e) { toast(e.message); }
        st.picked = [];
      }
      render();
      return;
    }
    st.focus = st.focus === id ? null : id;
    render();
    if (st.focus && window.innerWidth < 1100) setTimeout(() => $("#story", root)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" }), 60);
  }

  function bindMap(svg) {
    $$("[data-thread]", svg).forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); pickOrFocus(el.dataset.thread); }));
    $$(".bead", svg).forEach((el) => el.addEventListener("click", (e) => { e.stopPropagation(); beadSheet(e); }));
    $$(".nexus", svg).forEach((el) => {
      const open = (e) => { e.stopPropagation(); splitSheet(geo.nexus[el.dataset.nexus]); };
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") open(e); });
    });
    $$(".ghost-g", svg).forEach((el) => {
      const open = () => ghostSheet(data.ghosts[Number(el.dataset.ghost)]);
      el.addEventListener("click", open);
      el.addEventListener("keydown", (e) => { if (e.key === "Enter") open(); });
    });
    let dragging = false;
    const toTime = (e) => {
      const r = svg.getBoundingClientRect(), sx = geo.W / r.width;
      const pos = geo.vertical ? (e.clientY - r.top) * sx - geo.mT : (e.clientX - r.left) * sx - geo.mL;
      return geo.t0 + Math.min(1, Math.max(0, pos / geo.axisLen)) * geo.span;
    };
    svg.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".bead,.nexus,.ghost-g,[data-thread]")) return;
      dragging = true; svg.setPointerCapture(e.pointerId); moveCursor(toTime(e));
    });
    svg.addEventListener("pointermove", (e) => { if (dragging) moveCursor(toTime(e)); });
    svg.addEventListener("pointerup", () => { dragging = false; });
  }

  function placeCursor(t) {
    const svg = $("#map", root);
    const line = $("#cursorLine", svg), g = $("#cursorG", svg);
    if (!line) return;
    g.style.display = "inline";
    const [x, y] = geo.P(t, 0);
    if (geo.vertical) { line.setAttribute("x1", geo.mL - 6); line.setAttribute("x2", geo.W - 10); line.setAttribute("y1", y); line.setAttribute("y2", y); }
    else { line.setAttribute("x1", x); line.setAttribute("x2", x); line.setAttribute("y1", geo.mT - 6); line.setAttribute("y2", geo.mT + geo.crossLen + 4); }
    const ro = $("#readout", root), r = svg.getBoundingClientRect(), k = r.width / geo.W;
    const rows = geo.threads.filter((th) => geo.lines[th.thread_id]).map((th) => {
      const ended = t > ms(th.ended_at);
      let val;
      if (geo.metric) {
        const v = valueAt(th.series[geo.metric.id], t);
        val = ended ? `<span class="muted">ended</span>` : esc(fmtNum(v, geo.metric.unit));
      } else {
        const past = th.moments.filter((m) => ms(m.t) <= t && m.kind !== "start");
        val = ended ? `<span class="muted">${esc(th.outcome_label.slice(0, 22))}</span>` : esc((past.pop()?.headline || "under way").slice(0, 26));
      }
      return { th, val, v: geo.metric ? valueAt(th.series[geo.metric.id], t) : 0 };
    });
    if (geo.metric) rows.sort((a, b) => (b.v ?? -Infinity) - (a.v ?? -Infinity));
    ro.innerHTML = `<div class="rt">${esc(simTime(t))}</div>` +
      rows.slice(0, 9).map(({ th, val }) => `<div class="rr"><span><span class="dot" style="background:${colorOf(th)}"></span>${keyNo[th.thread_id]}. ${esc(th.label.slice(0, 18))}</span><span>${val}</span></div>`).join("");
    ro.hidden = false;
    const rw = ro.offsetWidth || 200;
    if (geo.vertical) { ro.style.left = "12px"; ro.style.top = `${Math.min(r.height - 160, y * k + 12)}px`; }
    else { ro.style.top = "10px"; ro.style.left = `${Math.min(r.width - rw - 8, Math.max(8, x * k + 12))}px`; }
  }
  const simTime = (t) => {
    const off = tzOffsetMin(data.horizon.start) * 60000;
    const d = new Date(t + off);
    const iso = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}T${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
    return fmtTime(iso, res, true);
  };
  function moveCursor(t) { st.cursor = t; placeCursor(t); }

  /* ---------------- sheets ---------------- */
  function momentHtml(t, m) {
    return `<div class="card" style="box-shadow:none">
      <div class="row between"><span class="eyebrow">${esc(KIND_WORD[m.kind] || m.kind)} · ${esc(fmtTime(m.t, res, true))}</span>${simStamp}</div>
      <h3 style="margin:8px 0 4px">${esc(m.headline)}</h3>
      <div class="row small muted"><span class="dot" style="background:${colorOf(t)}"></span> on path ${keyNo[t.thread_id]} · ${esc(t.label)}
        ${m.tier ? ` · ${tierMeter(m.tier)} ${esc(tierWords(m.tier))}` : ""}</div>
      ${m.rationale ? `<p class="small" style="margin-top:8px">“${esc(m.reasoning || m.rationale)}”</p><p class="tiny muted">The Agent's stated reasoning inside the simulation — not something the real person said.</p>
        ${m.reasoning && m.reasoning !== m.rationale ? `<details class="trace"><summary class="tiny muted">Exact trace</summary><p class="tiny mono muted">${esc(m.rationale)}</p></details>` : ""}` : ""}
      ${m.seq !== null ? `<div class="row" style="margin-top:10px"><button class="btn small" data-why="${t.rep_run_id}:${m.seq}">Why did this happen?</button>
        <button class="btn small ghost" data-promo="${t.rep_run_id}:${m.seq}">Try to treat it as real evidence</button></div><div data-out></div>` : ""}
    </div>`;
  }
  function bindSheetActions() {
    $$("#sheet [data-why]").forEach((b) => b.addEventListener("click", async () => {
      const [run, seq] = b.dataset.why.split(":");
      const out = b.closest(".card").querySelector("[data-out]");
      out.innerHTML = '<p class="small muted"><span class="spinner"></span> tracing causes…</p>';
      out.innerHTML = whyHtml(await api(`/v1/runs/${run}/why/${seq}`));
    }));
    $$("#sheet [data-promo]").forEach((b) => b.addEventListener("click", async () => {
      const [run, seq] = b.dataset.promo.split(":");
      const out = b.closest(".card").querySelector("[data-out]");
      const r = await api(`/v1/runs/${run}/events/${seq}/promotion-check`);
      out.innerHTML = `<div class="banner ${r.all_refused ? "sim" : "bad"}" style="margin-top:10px">${r.all_refused ? `All ${r.attempts.length} ways of turning this simulated event into real evidence or profile evidence were refused.` : "A promotion path was accepted — boundary breach."}</div>
        <ul class="small" style="margin:8px 0 0 18px;padding:0">${r.attempts.map((a) => `<li>${a.refused ? "✕" : "✓"} ${esc(a.path)}</li>`).join("")}</ul>`;
    }));
  }
  function beadSheet(e) {
    const svg = $("#map", root), r = svg.getBoundingClientRect(), k = geo.W / r.width;
    const cx = (e.clientX - r.left) * k, cy = (e.clientY - r.top) * k;
    const hits = [];
    for (const t of geo.threads) {
      if (!geo.lines[t.thread_id]) continue;
      for (const m of t.moments) {
        if (m.kind === "start" || m.kind === "end" || (t.parent && m.i < t.split_index)) continue;
        const c = geo.crossAt(t, ms(m.t));
        if (c === null) continue;
        const [x, y] = geo.P(ms(m.t), c);
        if (Math.hypot(x - cx, y - cy) < 11) hits.push([t, m]);
      }
    }
    if (!hits.length) return;
    openSheet(`<h2 id="sheet-title" style="margin-top:0">${hits.length > 1 ? `${hits.length} moments here` : "Moment"}</h2>${hits.slice(0, 8).map(([t, m]) => momentHtml(t, m)).join("")}`);
    bindSheetActions();
  }
  function splitSheet(n) {
    if (!n) return;
    const parent = geo.threads.find((t) => t.thread_id === n.kids[0].parent);
    openSheet(`<span class="eyebrow">Paths split · ${esc(fmtTime(n.kids[0].split_at, res, true))}</span>
      <h2 id="sheet-title" style="margin:6px 0 10px">What happened differently here</h2>
      ${n.kids.map((t) => `<div class="sides" style="margin-bottom:12px">
        <div class="side"><div class="row small"><span class="dot" style="background:${colorOf(parent)}"></span><b>${keyNo[parent.thread_id]}. ${esc(parent.label)}</b></div><p class="small">${esc(t.first_difference?.parent || "—")}</p></div>
        <div class="side"><div class="row small"><span class="dot" style="background:${colorOf(t)}"></span><b>${keyNo[t.thread_id]}. ${esc(t.label)}</b></div><p class="small">${esc(t.first_difference?.self || "—")}</p>
          ${t.carries.length ? `<p class="tiny muted">${esc(t.carries.join(" · "))}</p>` : ""}</div>
        <div class="row"><span class="tiny muted">${t.shared_events || 0} shared moments of history before this split</span>
        <button class="btn small" data-cmp="${parent.thread_id}|${t.thread_id}">Compare these two</button></div></div>`).join("")}`);
    $$("#sheet [data-cmp]").forEach((b) => b.addEventListener("click", async () => {
      closeSheet();
      st.picking = true; st.picked = [];
      const [a, bb] = b.dataset.cmp.split("|");
      await pickOrFocus(a); await pickOrFocus(bb);
    }));
  }
  function ghostSheet(g) {
    const what = { ruled_out: "Ruled out by an evidenced rule", set_aside: "Set aside by the budget — not infeasible", reopened: "Reopened after new evidence", unsimulated: "Identified, not simulated" }[g.kind];
    openSheet(`<span class="eyebrow">Possibility</span><h2 id="sheet-title" style="margin:6px 0 8px">${esc(g.label)}</h2>
      <p><span class="pill ${g.kind === "ruled_out" ? "bad" : ""}">${esc(what)}</span></p>
      <p class="lede" style="font-size:15px">${esc(humanize(g.why))}.</p>
      ${g.citation.length ? `<p class="small">Evidence cited: <span class="mono">${esc(g.citation.join(", "))}</span></p>` : ""}
      <p class="small muted">Nothing on the map should be read as exhaustive. Possibilities can exist that nobody identified.</p>`);
  }
  function whyHtml(w) {
    return `<div class="chain">${w.chain.map((s) => `<div><span class="mono tiny muted">${esc(fmtTime(s.sim_at, res, true))}</span> ${esc(s.headline || s.text.replace(/^At simulated \S+: /, ""))}
      ${s.rationale ? `<div class="tiny muted">Agent reasoning: “${esc((s.reasoning || s.rationale).slice(0, 220))}”</div>` : ""}
      ${(s.mechanisms || []).map((m) => `<div class="tiny">${tierMeter(m.tier)} ${esc(m.description || m.id)}</div>`).join("")}
      ${(s.random_draws || []).map((d) => `<div class="tiny muted">Random draw (${esc(d.stream)}): ${esc(JSON.stringify(d.value))}</div>`).join("")}</div>`).join("")}</div>
      <p class="tiny muted">${esc(w.limitations[0])}</p>`;
  }

  /* ---------------- story panel ---------------- */
  function chapterKey(iso) {
    if (res === "minute" || res === "hour") return `${iso.slice(11, 13)}:00`;
    const y = iso.slice(0, 4), m = Number(iso.slice(5, 7));
    if (res === "day" || res === "week") return fmtTime(iso, "day");
    return `Q${Math.ceil(m / 3)} ${y}`;
  }
  function sparkline(series, unit, end) {
    if (!series || series.length < 2) return "";
    const w = 120, h = 30, t0 = ms(series[0][0]), t1 = Math.max(ms(end), ms(series[series.length - 1][0]));
    const vs = series.map((p) => p[1]); const lo = Math.min(...vs, 0), hi = Math.max(...vs, lo + 1e-9);
    const X = (t) => ((ms(t) - t0) / Math.max(1, t1 - t0)) * w, Y = (v) => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
    let d = `M${X(series[0][0])},${Y(series[0][1])}`;
    for (let i = 1; i < series.length; i++) d += ` H${X(series[i][0])} V${Y(series[i][1])}`;
    d += ` H${w}`;
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><path d="${d}" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>`;
  }
  function renderStory() {
    const t = data.threads.find((x) => x.thread_id === st.focus);
    if (!t) return;
    const parentLabel = t.parent ? data.threads.find((x) => x.thread_id === t.parent) : null;
    const chapters = [];
    let lastKey = null;
    for (const m of t.moments) {
      if (m.kind === "start") continue;
      const key = chapterKey(m.t);
      if (key !== lastKey) { chapters.push({ key, items: [] }); lastKey = key; }
      chapters[chapters.length - 1].items.push(m);
    }
    const finalTier = t.tier_path.length ? t.tier_path[t.tier_path.length - 1][1] : null;
    const at = st.cursor && st.cursor < ms(t.ended_at) ? st.cursor : null;
    const story = $("#story", root);
    story.style.setProperty("--rail", colorOf(t));
    story.innerHTML = `
      <div class="row between"><span class="eyebrow">Path ${keyNo[t.thread_id]}</span><button class="btn small ghost" data-act="close">Close</button></div>
      <h2>${esc(t.label)}</h2>
      <div class="row"><span class="pill" style="color:${colorOf(t)}">${t.horizon_reached ? "○" : t.stopped_early ? "⋯" : "■"} ${esc(t.outcome_label)}</span>
        <span class="pill">${esc(t.detail === "detailed" ? "Detailed simulation" : t.detail === "preview" ? "Exploratory preview" : "Sensitivity probe")}</span>${simStamp}</div>
      <p class="synopsis" style="margin-top:12px">${esc(t.synopsis)}</p>
      ${parentLabel ? `<p class="small muted">${t.shared_events ? `Shares ${t.shared_events} moments of history with path ${keyNo[parentLabel.thread_id]}, then splits at ${esc(fmtTime(t.split_at, res, true))}` : `Differs from path ${keyNo[parentLabel.thread_id]} from the start`}: ${esc(t.first_difference?.self || "")}</p>` : ""}
      ${t.carries.length ? `<div class="chips" style="margin:10px 0">${t.carries.map((c) => `<span class="pill sim">${esc(c)}</span>`).join("")}</div>` : ""}
      ${data.metrics.length ? `<h3>${at ? `At ${esc(simTime(at))}` : "Where it ends up"}</h3>
        <dl class="kv">${data.metrics.map((m) => { const s = t.series[m.id]; const v = valueAt(s, at ?? ms(t.ended_at)); return `<dt>${esc(m.label)}<div style="color:${colorOf(t)}">${sparkline(s, m.unit, t.ended_at)}</div></dt><dd class="num">${esc(fmtNum(v, m.unit))}</dd>`; }).join("")}</dl>` : ""}
      <h3>Moments</h3>
      ${chapters.map((c) => `<div class="chapter">${esc(c.key)}</div><ol class="rail">${c.items.map((m) => `<li class="k-${m.kind}" style="--mk:${m.kind === "action" ? personColor(peopleIdx[m.actor] ?? 0) : m.kind === "threshold" ? "var(--warn)" : m.kind === "external" ? "var(--present)" : m.kind === "blocked" ? "var(--cut)" : colorOf(t)};${t.parent && m.i < t.split_index ? "opacity:.55" : ""}">
          <div class="rt">${esc(fmtTime(m.t, res, true))}${t.parent && m.i < t.split_index ? " · shared history" : ""}</div>
          <div class="rh">${esc(m.headline)}</div>
          ${m.rationale && m.kind !== "end" ? (() => { const r = m.reasoning || m.rationale; return `<div class="rq">“${esc(r.slice(0, 160))}${r.length > 160 ? "…" : ""}”</div>`; })() : m.kind === "end" && m.rationale ? `<div class="rq">${esc(m.rationale)}</div>` : ""}
          ${m.seq !== null ? `<button class="why" data-why="${t.rep_run_id}:${m.seq}">Why?</button><div data-out></div>` : ""}
        </li>`).join("")}</ol>`).join("")}
      <h3>This path rests on</h3>
      <p class="small">${finalTier ? `${tierMeter(finalTier)} ${esc(tierWords(finalTier))} (weakest support reached along the path).` : "—"}</p>
      <p class="tiny muted">Evidence support describes how well mechanisms are grounded — never how likely this future is.</p>
      <h3>Your reaction</h3>
      <p class="tiny muted">Only what you explicitly say counts as evidence about you. Looking at a path is not read as preference.</p>
      <div class="row"><button class="btn small" data-react="select">This path matters to me</button><button class="btn small ghost" data-react="correct">My Agent wouldn't do that…</button></div>
      <div id="fb"></div>
      <p class="tiny muted" style="margin-top:14px">Runs: ${t.run_ids.map((r) => `<a class="mono" href="#/run/${esc(r)}">${esc(r.slice(-6))}</a>`).join(", ")}</p>`;
    $('[data-act="close"]', story).addEventListener("click", () => { st.focus = null; render(); });
    $$("[data-why]", story).forEach((b) => b.addEventListener("click", async () => {
      const [run, seq] = b.dataset.why.split(":");
      const out = b.nextElementSibling;
      if (out.innerHTML) { out.innerHTML = ""; return; }
      out.innerHTML = '<span class="spinner"></span>';
      out.innerHTML = whyHtml(await api(`/v1/runs/${run}/why/${seq}`));
    }));
    $('[data-react="select"]', story).addEventListener("click", () => react(t, "EXPLICIT_SELECTION", { statement: "This path matters to me", preferred_path: t.label }));
    $('[data-react="correct"]', story).addEventListener("click", () => correctionSheet(t));
  }

  async function react(t, signal, payload) {
    const fb = $("#fb", root);
    try {
      const r = await api("/v1/human-evidence", { method: "POST", body: { decision_id: ctx.decisionId, signal_type: signal, payload, referenced_run_ids: t.run_ids, consent: { explicit_feedback: true } } });
      fb.innerHTML = `<div class="banner sim" style="margin-top:10px">Recorded as your own evidence${r.proposal ? ` — a suggested profile update is waiting for your approval in <a href="#/agent">Your Agent</a>` : ""}.</div>`;
    } catch (e) { fb.innerHTML = `<div class="banner bad">${esc(e.message)}</div>`; }
  }
  function correctionSheet(t) {
    openSheet(`<h2 id="sheet-title" style="margin-top:0">Correct how your Agent represents you</h2>
      <p class="small muted">This becomes a suggested update to your real profile. Nothing changes until you approve it.</p>
      <form class="stack" id="cf">
        <label>In your own words<input type="text" name="statement" placeholder="e.g. I will not accept terms that remove founder control" required></label>
        <details><summary>Advanced: structured feature</summary>
          <label>Feature key<input type="text" name="key" placeholder="constraint.founder_control_retained"></label>
          <label>Value (JSON)<input type="text" name="value" placeholder='{"op": "==", "value": true}'></label></details>
        <label class="toggle"><input type="checkbox" name="hard"> This is a hard boundary for me</label>
        <button class="btn primary" type="submit">Send correction</button>
      </form>`);
    $("#cf").addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const f = new FormData(ev.target);
      let value = f.get("value"); try { value = value ? JSON.parse(value) : null; } catch { /* keep text */ }
      closeSheet();
      await react(t, "EXPLICIT_CORRECTION", { statement: f.get("statement"), feature_key: f.get("key") || null, value, hard: !!f.get("hard") });
    });
  }

  /* ---------------- compare ---------------- */
  function renderCompare() {
    const { a, b, cmp } = st.compare;
    const A = data.threads.find((t) => t.thread_id === a), B = data.threads.find((t) => t.thread_id === b);
    const story = $("#story", root);
    const metrics = data.metrics.map((m) => {
      const va = valueAt(A.series[m.id], ms(A.ended_at)), vb = valueAt(B.series[m.id], ms(B.ended_at));
      const hi = Math.max(Math.abs(va || 0), Math.abs(vb || 0), 1e-9);
      return `<div class="pair"><div class="small">${esc(m.label)}</div><div class="bars">
        <div class="row"><div class="bar" style="width:${Math.max(2, (Math.abs(va || 0) / hi) * 100)}%;background:${colorOf(A)}"></div><span class="tiny num">${esc(fmtNum(va, m.unit))}</span></div>
        <div class="row"><div class="bar" style="width:${Math.max(2, (Math.abs(vb || 0) / hi) * 100)}%;background:${colorOf(B)}"></div><span class="tiny num">${esc(fmtNum(vb, m.unit))}</span></div></div></div>`;
    }).join("");
    const prefix = cmp.common_prefix_events;
    const firstAfter = (t) => t.moments.find((m) => m.i >= prefix && m.kind !== "start");
    const fa = firstAfter(A), fb = firstAfter(B);
    const carries = (t) => (t.carries.length ? t.carries : ["No extra assumptions"]).map((c) => `<span class="pill sim">${esc(c)}</span>`).join(" ");
    const policyDiff = cmp.intervention && cmp.intervention.policy_id ? `<p class="small">Different decision policy: <b>${esc(A.label.split(" × ")[0])}</b> vs <b>${esc(B.label.split(" × ")[0])}</b></p>` : "";
    const diffs = Object.keys(cmp.intervention || {}).length ? `${policyDiff}<div class="sides"><div class="side small"><b>${keyNo[a]}.</b> ${carries(A)}</div><div class="side small"><b>${keyNo[b]}.</b> ${carries(B)}</div></div>` : "";
    story.innerHTML = `<div class="row between"><span class="eyebrow">Compare</span><button class="btn small ghost" data-act="close">Close</button></div>
      <h2>Where two futures part</h2>
      ${cmp.banner ? `<div class="banner warn">${esc(cmp.banner)}</div>` : `<div class="banner sim small">Same world model and the same random draws: inside this model, differences come from what was changed on purpose.</div>`}
      <div class="splitcard" style="margin-top:12px">
        <p class="small">${cmp.common_prefix_events ? `Shared history: <b>${cmp.common_prefix_events}</b> events` : "They differ from the very start"}${cmp.first_divergence.a && cmp.common_prefix_events ? `, then they part at <b>${esc(fmtTime(cmp.first_divergence.a.sim_at, res, true))}</b>` : ""}.</p>
        <div class="sides">
          <div class="side"><div class="row small"><span class="dot" style="background:${colorOf(A)}"></span><b>${keyNo[a]}. ${esc(A.label)}</b></div>
            <p class="small">${esc(fa ? fa.headline : "no further events")}</p><p class="small" style="color:${colorOf(A)}">→ ${esc(A.outcome_label)}</p></div>
          <div class="side"><div class="row small"><span class="dot" style="background:${colorOf(B)}"></span><b>${keyNo[b]}. ${esc(B.label)}</b></div>
            <p class="small">${esc(fb ? fb.headline : "no further events")}</p><p class="small" style="color:${colorOf(B)}">→ ${esc(B.outcome_label)}</p></div>
        </div>
        ${diffs ? `<div><h3>Changed on purpose</h3>${diffs}</div>` : ""}
        ${metrics ? `<div><h3>Where they end up</h3>${metrics}</div>` : ""}
        <p class="tiny muted">${esc(cmp.limitations[0])}</p>
      </div>`;
    $('[data-act="close"]', story).addEventListener("click", () => { st.compare = null; render(); });
  }

  let lastW = 0;
  const ro = new ResizeObserver(() => { const w = root.clientWidth; if (Math.abs(w - lastW) > 30) { lastW = w; render(); } });
  ro.observe(root);
  render();
  return { destroy: () => ro.disconnect() };
}

/* Tiny static thumbnail for world cards. */
export function thumbnail(data) {
  const W = 320, H = 92, t0 = ms(data.horizon.start), t1 = ms(data.horizon.end);
  const threads = data.threads.filter((t) => t.detail !== "probe");
  if (!threads.length) return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%"><line x1="14" y1="10" x2="14" y2="82" stroke="var(--present)" stroke-width="2"/><text x="24" y="50" fill="var(--muted)" font-size="11">No futures explored yet</text></svg>`;
  const vis = visibleThreads({ ...data, threads }, { showPreviews: true, showProbes: false });
  const order = laneOrder(vis);
  const gap = Math.min(16, (H - 16) / order.length);
  const lane = Object.fromEntries(order.map((t, i) => [t.thread_id, 8 + (i + 0.5) * gap + (H - 16 - order.length * gap) / 2]));
  const famIndex = Object.fromEntries(data.families.map((f) => [f.key, f.color_index]));
  const X = (t) => 14 + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * (W - 30);
  let out = `<line x1="14" y1="6" x2="14" y2="${H - 6}" stroke="var(--present)" stroke-width="2" opacity=".9"/>`;
  for (const t of vis) {
    const y = lane[t.thread_id], c = outcomeColor(famIndex[t.family] ?? 0), xe = X(ms(t.ended_at));
    let d;
    if (!t.parent) d = `M14,${y} H${xe}`;
    else { const xs = X(ms(t.split_at)), y0 = lane[t.parent]; d = `M${xs},${y0} C${xs + 10},${y0} ${xs + 10},${y} ${xs + 20},${y} H${Math.max(xe, xs + 20)}`; }
    out += `<path d="${d}" fill="none" stroke="${c}" stroke-width="${t.detail === "detailed" ? 2 : 1.2}" stroke-opacity="${t.detail === "detailed" ? 1 : .6}"/>`;
    out += t.horizon_reached ? `<circle cx="${Math.max(xe, 30)}" cy="${y}" r="3" fill="var(--bg-2)" stroke="${c}" stroke-width="1.6"/>` : `<rect x="${Math.max(xe, 30) - 3}" y="${y - 3}" width="6" height="6" fill="${c}"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">${out}</svg>`;
}
