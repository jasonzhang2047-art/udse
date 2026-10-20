/* Shared helpers for the Futures client. */

export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};

export const session = { token: store.get("udse_token"), me: null };

export async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}) },
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

export async function waitJob(jobId, onTick) {
  for (let i = 0; i < 900; i++) {
    const j = await api(`/v1/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.status === "SUCCEEDED") return j.result;
    if (j.status === "FAILED") throw new Error(j.error || "job failed");
    await new Promise((r) => setTimeout(r, 600));
  }
  throw new Error("job timed out");
}

export const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** Format a simulated timestamp without converting time zones (the sim clock is authoritative). */
export function fmtTime(iso, res = "minute", long = false) {
  if (!iso || iso === "UNKNOWN") return iso || "";
  const y = iso.slice(0, 4), mo = Number(iso.slice(5, 7)), d = Number(iso.slice(8, 10)), hm = iso.slice(11, 16);
  if (res === "minute" || res === "hour") return long ? `${d} ${MONTHS[mo - 1]} ${hm}` : hm;
  if (res === "day" || res === "week") return `${d} ${MONTHS[mo - 1]}${long ? " " + y : ""}`;
  return long ? `${d} ${MONTHS[mo - 1]} ${y}` : `${MONTHS[mo - 1]} ${y}`;
}
export const ms = (iso) => Date.parse(iso);

export function fmtNum(v, unit) {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const abs = Math.abs(v);
  let s;
  if (abs >= 1e6) s = (v / 1e6).toFixed(abs >= 1e7 ? 1 : 2).replace(/\.0+$/, "") + "M";
  else if (abs >= 1e4) s = (v / 1e3).toFixed(abs >= 1e5 ? 0 : 1).replace(/\.0$/, "") + "k";
  else s = Number(v).toLocaleString(undefined, { maximumFractionDigits: abs < 10 ? 2 : 1 });
  if (!unit || unit === "1") return s;
  if (unit === "USD") return "$" + s;
  if (unit === "%") return s + "%";
  return `${s} ${unit === "month" ? (Math.abs(v) === 1 ? "month" : "months") : unit}`;
}

export function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (v === "UNKNOWN") return '<span class="pill warn">unknown</span>';
  if (typeof v === "object" && "value" in v) return esc(fmtNum(v.value, v.unit));
  if (typeof v === "number") return esc(fmtNum(v));
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v === "string" && /^\d{4}-\d\d-\d\dT/.test(v)) return esc(fmtTime(v, "minute", true));
  return esc(String(v).replace(/_/g, " "));
}

export const TIERS = {
  A_GROUNDED: { n: 4, word: "Grounded in evidence", short: "Grounded" },
  B_ASSUMPTION_BOUND: { n: 3, word: "Rests on stated assumptions", short: "Assumption-bound" },
  C_ILLUSTRATIVE: { n: 2, word: "Illustrative assumptions", short: "Illustrative" },
  X_LIMITED_OR_ABSTAIN: { n: 0, word: "Not modelled responsibly", short: "Withheld" },
};
export const tierMeter = (t) => {
  const n = (TIERS[t] || { n: 0 }).n;
  return `<span class="meter t${n}" title="${esc((TIERS[t] || {}).word || "")} — evidence support, not forecast accuracy"><i></i><i></i><i></i><i></i></span>`;
};
export const tierWords = (t) => (TIERS[t] || { word: "—" }).word;

export const outcomeColor = (i) => `var(--o${((i % 8) + 8) % 8})`;
export const personColor = (i) => `var(--p${((i % 6) + 6) % 6})`;
export const initials = (name) => (name || "?").replace(/\(.*?\)/g, "").trim().split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

export function humanize(s) {
  s = String(s || "").replace(/[_.]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function openSheet(html) { $("#sheet-body").innerHTML = html; $("#sheet").hidden = false; $(".sheet-close").focus(); }
export function closeSheet() { $("#sheet").hidden = true; }
export function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 3200);
}
export const errorBox = (e) => `<div class="banner bad">${esc(e.message || e)}</div>`;
export const simStamp = '<span class="stamp" title="Simulation output — not a real-world event">simulated</span>';
