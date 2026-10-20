/* Formatting and plain-language helpers (mirrors src/udse/web/static/util.js). */

import type { Tier, Value } from './types';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const ms = (iso: string) => Date.parse(iso);

/** Format a simulated timestamp without converting time zones (the simulation clock is authoritative). */
export function fmtTime(iso: string | null | undefined, res = 'minute', long = false): string {
  if (!iso || iso === 'UNKNOWN') return iso || '';
  const y = iso.slice(0, 4);
  const mo = Number(iso.slice(5, 7));
  const d = Number(iso.slice(8, 10));
  const hm = iso.slice(11, 16);
  if (res === 'minute' || res === 'hour') return long ? `${d} ${MONTHS[mo - 1]} ${hm}` : hm;
  if (res === 'day' || res === 'week') return `${d} ${MONTHS[mo - 1]}${long ? ' ' + y : ''}`;
  return long ? `${d} ${MONTHS[mo - 1]} ${y}` : `${MONTHS[mo - 1]} ${y}`;
}

/** Minutes east of UTC encoded in an ISO timestamp's suffix. */
export function tzOffsetMin(iso: string): number {
  const m = /([+-])(\d\d):(\d\d)$/.exec(iso || '');
  return m ? (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) : 0;
}

/** A millisecond instant rendered in the simulation's own offset (taken from a reference timestamp). */
export function simTimeAt(t: number, refIso: string, res: string): string {
  const off = tzOffsetMin(refIso) * 60000;
  const d = new Date(t + off);
  const p = (n: number) => String(n).padStart(2, '0');
  const iso = `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
  return fmtTime(iso, res, true);
}

/** Display resolution for a horizon when the decision scope isn't at hand: clock times for sub-day spans. */
export function resolutionFor(start: string, end: string): string {
  const span = ms(end) - ms(start);
  return span <= 2 * 864e5 ? 'minute' : span <= 120 * 864e5 ? 'day' : 'month';
}

export const monthName = (i: number) => MONTHS[((i % 12) + 12) % 12];

export function fmtNum(v: number | null | undefined, unit?: string | null): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const abs = Math.abs(v);
  let s: string;
  if (abs >= 1e6) s = (v / 1e6).toFixed(abs >= 1e7 ? 1 : 2).replace(/\.0+$/, '') + 'M';
  else if (abs >= 1e4) s = (v / 1e3).toFixed(abs >= 1e5 ? 0 : 1).replace(/\.0$/, '') + 'k';
  else s = Number(v).toLocaleString(undefined, { maximumFractionDigits: abs < 10 ? 2 : 1 });
  if (!unit || unit === '1') return s;
  if (unit === 'USD') return '$' + s;
  if (unit === '%') return s + '%';
  return `${s} ${unit === 'month' ? (Math.abs(v) === 1 ? 'month' : 'months') : unit}`;
}

export const isQuantity = (v: unknown): v is { value: number; unit: string | null } =>
  !!v && typeof v === 'object' && !Array.isArray(v) && 'value' in (v as object) && typeof (v as { value: unknown }).value === 'number';

/** Plain text for any API value; `unit` applies to bare numbers. */
export function fmtVal(v: Value | undefined, unit?: string | null): string {
  if (v === null || v === undefined) return '—';
  if (v === 'UNKNOWN') return 'unknown';
  if (isQuantity(v)) return fmtNum(v.value, v.unit);
  if (typeof v === 'number') return fmtNum(v, unit);
  if (typeof v === 'boolean') return v ? 'yes' : 'no';
  if (typeof v === 'string' && /^\d{4}-\d\d-\d\dT/.test(v)) return fmtTime(v, 'minute', true);
  if (typeof v === 'string') return v.replace(/_/g, ' ');
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    // Constraint and comparison shapes used by facts and profile features.
    if ('op' in o && 'value' in o) return `${String(o.op)} ${fmtVal(o.value as Value)}`;
    if ('min' in o || 'max' in o) {
      const lo = typeof o.min === 'number' ? o.min : null;
      const hi = typeof o.max === 'number' ? o.max : null;
      if (lo !== null && hi !== null) return `${fmtNum(lo, unit)}–${fmtNum(hi, unit)}`;
      if (lo !== null) return `≥ ${fmtNum(lo, unit)}`;
      if (hi !== null) return `≤ ${fmtNum(hi, unit)}`;
    }
  }
  return JSON.stringify(v);
}

export function humanize(s: string | null | undefined): string {
  const t = String(s || '')
    .replace(/[_.]+/g, ' ')
    .trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export const initials = (name: string | null | undefined) =>
  (name || '?')
    .replace(/\(.*?\)/g, '')
    .trim()
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join('')
    .toUpperCase();

/** Evidence tiers describe how well a mechanism is grounded — never how likely an outcome is. */
export const TIERS: Record<Tier, { n: number; word: string; short: string }> = {
  A_GROUNDED: { n: 4, word: 'Grounded in evidence', short: 'Grounded' },
  B_ASSUMPTION_BOUND: { n: 3, word: 'Rests on stated assumptions', short: 'Assumption-bound' },
  C_ILLUSTRATIVE: { n: 2, word: 'Illustrative assumptions', short: 'Illustrative' },
  X_LIMITED_OR_ABSTAIN: { n: 0, word: 'Not modelled responsibly', short: 'Withheld' },
};
export const tierWords = (t: string | null | undefined) => (t && TIERS[t as Tier]?.word) || '—';
export const tierShort = (t: string | null | undefined) => (t && TIERS[t as Tier]?.short) || t || '—';

export const DETAIL_WORD = { detailed: 'Detailed simulation', preview: 'Exploratory preview', probe: 'Sensitivity probe' } as const;

/** Run statuses in plain words (docs/design/ui-redesign.md §6). */
export function statusWords(status: string | null | undefined): string {
  switch (status) {
    case 'COMPLETED_HORIZON':
      return 'Reached the horizon';
    case 'TERMINAL_STATE':
      return 'Reached an end state';
    case 'BUDGET_EXHAUSTED':
      return 'Stopped early — not simulated after this';
    case 'MODEL_EXECUTION_BLOCKED':
      return 'Blocked by the model';
    case 'FAILED':
      return 'Failed';
    default:
      return humanize((status || '').toLowerCase());
  }
}

const OUTCOME_WORDS: Record<string, string> = {
  CASH_EXHAUSTED: 'Ran out of cash',
  NO_AGREEMENT_BY_DEADLINE: 'No agreement by the decide-by time',
  DINED: 'Dined together',
  DEFERRED: 'Stayed in',
};
export const outcomeWords = (code: string | null | undefined) => (code ? OUTCOME_WORDS[code] || humanize(code.toLowerCase()) : '');

export const shortHost = (uri: string | null | undefined) => {
  if (!uri) return 'you';
  const m = /^[a-z]+:\/\/([^/]+)/i.exec(uri);
  return m ? m[1] : uri;
};
