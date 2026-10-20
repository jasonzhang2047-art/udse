/* Futures-map geometry helpers (docs/design/ui-redesign.md §3, §10.1; ported from web/static/futures.js).
 * Pure functions: layout happens in abstract (time, cross) space and is projected by the renderer. */

import { monthName, ms, tzOffsetMin } from './format';
import type { Compare, Futures, Thread } from './types';

export interface PlacedThread extends Thread {
  parent: string | null;
}

/** Threads to draw, re-parenting through hidden ones so every visible thread still splits from a visible one. */
export function visibleThreads(
  data: Pick<Futures, 'threads' | 'horizon'>,
  opts: { showPreviews: boolean; showProbes: boolean; compare?: { a: string; b: string; cmp: Compare | null } | null },
): PlacedThread[] {
  const byId = Object.fromEntries(data.threads.map((t) => [t.thread_id, t]));
  const trunk = data.threads[0];
  if (!trunk) return [];
  if (opts.compare) {
    const a = byId[opts.compare.a];
    const b = byId[opts.compare.b];
    const cmp = opts.compare.cmp;
    const fd = cmp?.first_divergence;
    const split = fd?.b?.sim_at ?? fd?.a?.sim_at ?? b.split_at;
    return [
      { ...a, parent: null, split_at: data.horizon.start, split_index: 0 },
      { ...b, parent: a.thread_id, split_at: split, split_index: cmp ? cmp.common_prefix_events : b.split_index },
    ];
  }
  const keep = (t: Thread) =>
    t === trunk || t.detail === 'detailed' || (t.detail === 'preview' && opts.showPreviews) || (t.detail === 'probe' && opts.showProbes);
  const vis = data.threads.filter(keep);
  const visSet = new Set(vis.map((t) => t.thread_id));
  return vis.map((t) => {
    if (t === trunk) return { ...t, parent: null };
    let p = t.parent;
    let split = t.split_at;
    let idx = t.split_index;
    while (p && !visSet.has(p)) {
      const pt = byId[p];
      if (ms(pt.split_at) < ms(split)) {
        split = pt.split_at;
        idx = Math.min(idx, pt.split_index);
      }
      p = pt.parent;
    }
    return { ...t, parent: p || trunk.thread_id, split_at: split, split_index: idx };
  });
}

/** Lane order: DFS over the split tree, children ordered by split time, alternating sides of their parent. */
export function laneOrder(threads: PlacedThread[]): PlacedThread[] {
  const kids: Record<string, PlacedThread[]> = {};
  threads.forEach((t) => {
    if (t.parent) (kids[t.parent] = kids[t.parent] || []).push(t);
  });
  const order = (node: PlacedThread): PlacedThread[] => {
    const ks = (kids[node.thread_id] || []).slice().sort((a, b) => ms(b.split_at) - ms(a.split_at));
    const above: PlacedThread[] = [];
    const below: PlacedThread[] = [];
    ks.forEach((k, i) => (i % 2 === 0 ? below : above).push(k));
    return [...above.reverse().flatMap(order), node, ...below.flatMap(order)];
  };
  const root = threads.find((t) => !t.parent);
  return root ? order(root) : [];
}

/** Last committed value at or before time t (step semantics: never interpolated). */
export function valueAt(series: [string, number][] | undefined, t: number): number | null {
  if (!series || !series.length) return null;
  let v = series[0][1];
  for (const [ti, vi] of series) {
    if (ms(ti) <= t) v = vi;
    else break;
  }
  return v;
}

export const smooth = (x: number) => x * x * (3 - 2 * x);

/** Split a time-ordered polyline at time b, interpolating the cut point. */
export function splitAt(pts: [number, number][], b: number): [[number, number][], [number, number][]] {
  const left: [number, number][] = [];
  const right: [number, number][] = [];
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (p[0] <= b) left.push(p);
    if (p[0] >= b) right.push(p);
    const q = pts[i + 1];
    if (q && p[0] < b && q[0] > b) {
      const f = (b - p[0]) / (q[0] - p[0]);
      const m: [number, number] = [b, p[1] + f * (q[1] - p[1])];
      left.push(m);
      right.push(m);
    }
  }
  return [left, right];
}

/** Axis ticks adapted to the horizon: minutes/hours for an evening, months/quarters for years. */
export function makeTicks(h: { start: string; end: string; resolution: string }, axisPx: number): { t: number; label: string }[] {
  const s = ms(h.start);
  const e = ms(h.end);
  const off = tzOffsetMin(h.start) * 60000;
  const target = Math.max(3, Math.floor(axisPx / 90));
  const out: { t: number; label: string }[] = [];
  const p2 = (n: number) => String(n).padStart(2, '0');
  if (h.resolution === 'minute' || h.resolution === 'hour') {
    const step = [15, 30, 60, 120, 180, 360, 720].map((m) => m * 60000).find((st) => (e - s) / st <= target) || 720 * 60000;
    for (let t = Math.ceil((s + off) / step) * step - off; t <= e; t += step) {
      const d = new Date(t + off);
      out.push({ t, label: `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}` });
    }
    return out;
  }
  if (h.resolution === 'day' || h.resolution === 'week') {
    const step = [1, 2, 7, 14, 28].map((d) => d * 864e5).find((st) => (e - s) / st <= target) || 28 * 864e5;
    for (let t = Math.ceil((s + off) / 864e5) * 864e5 - off; t <= e; t += step) {
      const d = new Date(t + off);
      out.push({ t, label: `${d.getUTCDate()} ${monthName(d.getUTCMonth())}` });
    }
    return out;
  }
  const months = (e - s) / (30.44 * 864e5);
  const stepM = [1, 2, 3, 6, 12].find((m) => months / m <= target) || 12;
  const sd = new Date(s + off);
  const y = sd.getUTCFullYear();
  let mo = Math.ceil((sd.getUTCMonth() + 1) / stepM) * stepM;
  for (let i = 0; i < 200; i++) {
    const yy = y + Math.floor(mo / 12);
    const mm = mo % 12;
    const t = Date.UTC(yy, mm, 1) - off;
    if (t > e) break;
    if (t >= s) out.push({ t, label: mm === 0 || out.length === 0 ? `${monthName(mm)} ${yy}` : monthName(mm) });
    mo += stepM;
  }
  return out;
}
