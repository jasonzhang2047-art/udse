/* Futures map (docs/design/ui-redesign.md §3, §5.3): simulated threads branch from the amber Present line
 * at their first observable difference. Time flows down on phones (< 640pt) and to the right otherwise.
 * Segment stroke = weakest evidence tier reached so far ("fraying"); colour = outcome family. */

import { useEffect, useId, useMemo, useRef } from 'react';
import { Animated, Platform, View } from 'react-native';
import Svg, { Circle, G, Line, Path, Rect, Text as SvgText, TSpan } from 'react-native-svg';

import { fmtNum, fmtTime, ms } from '@/lib/format';
import { laneOrder, makeTicks, smooth, splitAt, valueAt, visibleThreads, type PlacedThread } from '@/lib/futures';
import { useTheme } from '@/lib/theme';
import type { Compare, Futures, Ghost, Moment, Tier } from '@/lib/types';

import { T } from './ui';

const TIER_STYLE: Record<Tier, { dash?: string; op: number }> = {
  A_GROUNDED: { op: 1 },
  B_ASSUMPTION_BOUND: { op: 0.82 },
  C_ILLUSTRATIVE: { dash: '8 5', op: 0.9 },
  X_LIMITED_OR_ABSTAIN: { dash: '1.5 6', op: 0.9 },
};
const WIDTH = { detailed: 2.8, preview: 1.7, probe: 1.3 } as const;

export interface MapOptions {
  lens: string;
  showPreviews: boolean;
  showProbes: boolean;
  showGhosts: boolean;
  focus: string | null;
  compare: { a: string; b: string; cmp: Compare | null } | null;
  picked: string[];
}

export interface Nexus {
  key: string;
  x: number;
  y: number;
  kids: PlacedThread[];
}

type Pt = [number, number];

/** Geometry in abstract (time, cross) space, projected by `P`. Shared by the map and the readout. */
export function layoutMap(data: Futures, o: MapOptions, W: number) {
  const vertical = W < 640;
  const threads = visibleThreads(data, o);
  const order = laneOrder(threads);
  const lane: Record<string, number> = Object.fromEntries(order.map((t, i) => [t.thread_id, i]));
  const t0 = ms(data.horizon.start);
  const t1 = ms(data.horizon.end);
  const span = Math.max(1, t1 - t0);
  const metric = o.lens !== 'structure' ? data.metrics.find((m) => m.id === o.lens) || null : null;
  const ghostsOn = o.showGhosts && !vertical && !o.compare && !metric;
  const ghosts = ghostsOn ? data.ghosts.slice(0, 6) : [];
  const gAbove = Math.ceil(ghosts.length / 2);
  const gBelow = Math.floor(ghosts.length / 2);
  const wideLabels = !vertical && W > 820;
  const mL = vertical ? 54 : 20;
  const mR = vertical ? 18 : wideLabels ? 236 : 44;
  const mT = vertical ? 38 : 26;
  const mB = vertical ? 40 : 34;
  const n = Math.max(order.length, 1);
  const laneGap = vertical ? Math.max(24, Math.min(46, (W - mL - mR) / n)) : Math.min(72, Math.max(order.length > 10 ? 30 : 38, 200 / n));
  const ghostGap = 26;
  let H: number, axisLen: number, crossLen: number;
  if (vertical) {
    axisLen = Math.max(560, Math.min(980, order.length * 60 + 420));
    H = mT + axisLen + mB;
    crossLen = W - mL - mR;
  } else {
    crossLen = metric ? Math.max(300, order.length * 30) : order.length * laneGap + (gAbove + gBelow) * ghostGap;
    H = mT + crossLen + mB;
    axisLen = W - mL - mR;
  }
  const tPx = (t: number) => ((Math.min(Math.max(t, t0), t1) - t0) / span) * axisLen;

  let vMin = 0;
  let vMax = 1;
  let logScale = false;
  const sl = (v: number) => Math.sign(v) * Math.log10(1 + Math.abs(v));
  const isl = (u: number) => Math.sign(u) * (Math.pow(10, Math.abs(u)) - 1);
  if (metric) {
    const vals = threads.flatMap((t) => (t.series[metric.id] || []).map((p) => p[1]));
    const pos = vals.filter((v) => v > 0).sort((a, b) => a - b);
    const med = pos.length ? pos[Math.floor(pos.length / 2)] : 0;
    logScale = vals.length > 0 && med > 0 && Math.max(...vals) / med > 15 && Math.min(...vals) >= 0;
    const tf = logScale ? sl : (v: number) => v;
    vMin = Math.min(0, ...vals.map(tf));
    vMax = Math.max(...vals.map(tf), vMin + 1e-9);
    const pad = (vMax - vMin) * 0.06;
    vMax += pad;
    if (vMin < 0) vMin -= pad;
  }
  const tfm = logScale ? sl : (v: number) => v;
  const laneX = (l: number) => (vertical ? (l + 0.5) * laneGap + (crossLen - order.length * laneGap) / 2 : gAbove * ghostGap + (l + 0.5) * laneGap);
  const valX = (raw: number) => {
    const f = (tfm(raw) - vMin) / (vMax - vMin);
    return vertical ? f * crossLen : crossLen - f * crossLen;
  };
  const P = (t: number, c: number): Pt => (vertical ? [mL + c, mT + tPx(t)] : [mL + tPx(t), mT + c]);
  const curveT = Math.min(span * 0.05, span * (60 / axisLen));

  const lines: Record<string, Pt[] | null> = {};
  for (const t of threads) {
    const own = laneX(lane[t.thread_id]);
    const tS = t.parent ? ms(t.split_at) : t0;
    const tE = Math.max(ms(t.ended_at), tS);
    let pts: Pt[] = [];
    if (metric) {
      const s = t.series[metric.id] || [];
      let v = valueAt(s, tS);
      if (v === null) {
        lines[t.thread_id] = null;
        continue;
      }
      pts.push([tS, valX(v)]);
      for (const [ti, vi] of s) {
        const tm = ms(ti);
        if (tm <= tS) continue;
        if (tm > tE) break;
        pts.push([tm, valX(v)], [tm, valX(vi)]);
        v = vi;
      }
      pts.push([tE, valX(v)]);
    } else if (!t.parent) {
      pts = [
        [t0, own],
        [tE, own],
      ];
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
  const crossAt = (t: PlacedThread, time: number): number | null => {
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

  const nexus: Record<string, Nexus> = {};
  for (const t of threads) {
    if (!t.parent || !lines[t.thread_id]) continue;
    const parent = threads.find((x) => x.thread_id === t.parent);
    if (!parent) continue;
    const c = metric ? crossAt(parent, ms(t.split_at)) : laneX(lane[t.parent]);
    if (c === null) continue;
    const [x, y] = P(ms(t.split_at), c);
    const k = `${Math.round(x / 4)}:${Math.round(y / 4)}`;
    (nexus[k] = nexus[k] || { key: k, x, y, kids: [] }).kids.push(t);
  }

  return {
    vertical,
    threads,
    order,
    lane,
    t0,
    t1,
    span,
    metric,
    ghosts,
    gAbove,
    wideLabels,
    mL,
    mR,
    mT,
    mB,
    laneGap,
    ghostGap,
    H,
    W,
    axisLen,
    crossLen,
    tPx,
    P,
    laneX,
    valX,
    logScale,
    vMin,
    vMax,
    isl,
    lines,
    crossAt,
    nexus,
  };
}
export type MapLayout = ReturnType<typeof layoutMap>;

export function FuturesMap({
  data,
  o,
  width,
  cursor,
  keyNo,
  onThread,
  onBead,
  onNexus,
  onGhost,
}: {
  data: Futures;
  o: MapOptions;
  width: number;
  cursor: number | null;
  keyNo: Record<string, number>;
  onThread: (id: string) => void;
  onBead: (t: PlacedThread, m: Moment) => void;
  onNexus: (n: Nexus) => void;
  onGhost: (g: Ghost) => void;
}) {
  const { c, isDark, outcome, person, reduceMotion } = useTheme();
  const L = useMemo(() => layoutMap(data, o, width), [data, o, width]);
  /* Native: onPress per element. Web: react-native-svg's web build leaks responder props onto DOM nodes
   * when onPress is used, so the web uses one delegated click handler keyed by element id instead. */
  const uid = useId();
  const handlers = useRef<Record<string, () => void>>({});
  handlers.current = {};
  const press = (key: string, fn: () => void): { onPress?: () => void; id?: string } => {
    if (Platform.OS !== 'web') return { onPress: fn };
    const id = `${uid}|${key}`;
    handlers.current[id] = fn;
    return { id };
  };
  const webClick =
    Platform.OS === 'web'
      ? ({
          onClick: (e: { target: unknown; currentTarget: unknown }) => {
            let el = e.target as { id?: string; parentNode?: unknown } | null;
            while (el && el !== e.currentTarget) {
              if (el.id && handlers.current[el.id]) return handlers.current[el.id]();
              el = el.parentNode as typeof el;
            }
          },
        } as object)
      : {};
  const fade = useRef(new Animated.Value(reduceMotion ? 1 : 0)).current;
  useEffect(() => {
    if (reduceMotion) fade.setValue(1);
    else Animated.timing(fade, { toValue: 1, duration: 600, useNativeDriver: false }).start();
  }, [fade, reduceMotion]);

  const fam = Object.fromEntries(data.families.map((f) => [f.key, f.color_index]));
  const people = Object.fromEntries(data.people.map((p, i) => [p.id, i]));
  const colorOf = (t: { family: string }) => outcome(fam[t.family] ?? 0);
  const res = data.horizon.resolution;
  const { P, vertical, W, H, mL, mR, mT, crossLen, axisLen, metric } = L;
  const focusOn = !!o.focus && !o.compare;
  const dimOp = (id: string) => (focusOn && o.focus !== id ? 0.2 : 1);
  const d = (pts: Pt[]) =>
    'M' +
    pts
      .map(([tt, cc]) =>
        P(tt, cc)
          .map((v) => v.toFixed(1))
          .join(','),
      )
      .join(' L');

  const drawOrder = [...L.threads].sort(
    (a, b) => Number(a.thread_id === o.focus) - Number(b.thread_id === o.focus) || Number(a.detail === 'detailed') - Number(b.detail === 'detailed'),
  );

  const summary =
    `Futures map: ${L.threads.length} simulated paths from ${fmtTime(data.horizon.start, res, true)} to ${fmtTime(data.horizon.end, res, true)}. ` +
    L.threads.map((t) => `Path ${keyNo[t.thread_id]}, ${t.label}: ${t.outcome_label}`).join('. ') +
    '. The path list below the map has the same information.';

  const els: React.ReactNode[] = [];

  /* ticks */
  makeTicks(data.horizon, axisLen).forEach((tk, i) => {
    const [x, y] = P(tk.t, 0);
    if (vertical) {
      els.push(<Line key={`tk${i}`} x1={mL} y1={y} x2={W - mR} y2={y} stroke={c.line} strokeWidth={1} />);
      els.push(
        <SvgText key={`tl${i}`} x={mL - 8} y={y + 4} textAnchor="end" fill={c.muted} fontSize={11}>
          {tk.label}
        </SvgText>,
      );
    } else {
      els.push(<Line key={`tk${i}`} x1={x} y1={mT} x2={x} y2={mT + crossLen} stroke={c.line} strokeWidth={1} />);
      els.push(
        <SvgText key={`tl${i}`} x={x} y={H - 12} textAnchor="middle" fill={c.muted} fontSize={11}>
          {tk.label}
        </SvgText>,
      );
    }
  });
  /* metric axis */
  if (metric) {
    if (L.logScale)
      els.push(
        <SvgText
          key="log"
          x={vertical ? W - mR : mL + axisLen + 6}
          y={mT - 12}
          textAnchor={vertical ? 'end' : 'start'}
          fill={c.muted}
          fontSize={11}
          fontStyle="italic"
        >
          log scale
        </SvgText>,
      );
    [0, 0.25, 0.5, 0.75, 1].forEach((f, i) => {
      const u = L.vMin + f * (L.vMax - L.vMin);
      const v = L.logScale ? L.isl(u) : u;
      const cc = L.valX(v);
      if (vertical)
        els.push(
          <SvgText key={`mv${i}`} x={mL + cc} y={mT - 14} textAnchor="middle" fill={c.muted} fontSize={10}>
            {fmtNum(v, metric.unit)}
          </SvgText>,
        );
      else {
        els.push(<Line key={`mg${i}`} x1={mL} y1={mT + cc} x2={mL + axisLen} y2={mT + cc} stroke={c.line} strokeDasharray="2 4" />);
        els.push(
          <SvgText key={`mv${i}`} x={mL + axisLen + 6} y={mT + cc + 4} fill={c.muted} fontSize={11}>
            {fmtNum(v, metric.unit)}
          </SvgText>,
        );
      }
    });
  }
  /* present + horizon */
  const [px0, py0] = P(L.t0, 0);
  const [px1, py1] = P(L.t1, 0);
  if (vertical) {
    els.push(<Line key="hz" x1={mL - 4} y1={py1} x2={W - mR} y2={py1} stroke={c.muted} strokeDasharray="5 5" />);
    els.push(
      <SvgText key="hzl" x={W - mR} y={py1 + 16} textAnchor="end" fill={c.muted} fontSize={11}>
        {`horizon ${fmtTime(data.horizon.end, res, true)}`}
      </SvgText>,
    );
    els.push(<Line key="now" x1={mL - 6} y1={py0} x2={W - mR} y2={py0} stroke={c.present} strokeWidth={2.5} />);
    els.push(
      <SvgText key="nowl" x={W - mR} y={metric ? py0 + 15 : py0 - 8} textAnchor="end" fill={c.present} fontSize={10.5} fontWeight="bold" letterSpacing={1}>
        NOW · SIMULATION STARTS
      </SvgText>,
    );
  } else {
    els.push(<Line key="hz" x1={px1} y1={mT - 10} x2={px1} y2={mT + crossLen} stroke={c.muted} strokeDasharray="5 5" />);
    els.push(
      <SvgText key="hzl" x={px1} y={mT - 12} textAnchor="middle" fill={c.muted} fontSize={11}>
        horizon
      </SvgText>,
    );
    els.push(<Line key="now" x1={px0} y1={mT - 14} x2={px0} y2={mT + crossLen + 4} stroke={c.present} strokeWidth={2.5} />);
    els.push(
      <SvgText key="nowl" x={px0 + 6} y={mT - 12} fill={c.present} fontSize={10.5} fontWeight="bold" letterSpacing={1}>
        NOW · SIMULATION STARTS
      </SvgText>,
    );
  }
  /* ghosts: identified, not simulated (dotted stubs near the present; ruled out ends in a cut mark) */
  if (L.ghosts.length) {
    const trunk = L.threads.find((t) => !t.parent);
    const from = trunk ? L.laneX(L.lane[trunk.thread_id]) : 0;
    L.ghosts.forEach((g, i) => {
      const above = i % 2 === 0;
      const k = Math.floor(i / 2);
      const cc = above ? (L.gAbove - 1 - k + 0.5) * L.ghostGap : L.gAbove * L.ghostGap + L.order.length * L.laneGap + (k + 0.5) * L.ghostGap;
      const len = L.span * (g.kind === 'ruled_out' ? 0.07 : 0.11);
      const pts: Pt[] = [];
      for (let j = 0; j <= 12; j++) {
        const f = j / 12;
        pts.push(P(L.t0 + f * len, from + (cc - from) * smooth(f)));
      }
      const [ex, ey] = pts[pts.length - 1];
      const path = 'M' + pts.map((p) => p.join(',')).join(' L');
      const why = g.kind === 'ruled_out' ? 'ruled out' : g.kind === 'set_aside' ? 'set aside' : g.why.length > 34 ? g.why.slice(0, 34) + '…' : g.why;
      els.push(
        <G key={`g${i}`} {...press(`ghost:${i}`, () => onGhost(g))}>
          <Path d={path} fill="none" stroke={g.kind === 'ruled_out' ? c.cut : c.ghost} strokeWidth={1.6} strokeDasharray="2 5" />
          {g.kind === 'ruled_out' ? (
            <Path d={`M${ex - 4},${ey - 6} L${ex + 4},${ey + 6} M${ex + 4},${ey - 6} L${ex - 4},${ey + 6}`} stroke={c.cut} strokeWidth={2} />
          ) : null}
          <SvgText x={ex + 10} y={ey + 4} fill={c.muted} fontSize={11}>
            {g.label}
            <TSpan fillOpacity={0.7}>{` — ${why}`}</TSpan>
          </SvgText>
          <Path d={path} fill="none" stroke={c.bg} strokeOpacity={0.01} strokeWidth={16} />
        </G>,
      );
    });
  }
  /* threads: tier-styled segments */
  for (const t of drawOrder) {
    const pts = L.lines[t.thread_id];
    if (!pts) continue;
    const tS = pts[0][0];
    const breaks = (t.tier_path || []).map(([ti, tier]) => [ms(ti), tier] as [number, Tier]);
    const segs: [Pt[], Tier][] = [];
    let rest = pts;
    let cur: Tier = breaks.filter(([b]) => b <= tS).pop()?.[1] || (breaks[0] ? breaks[0][1] : 'B_ASSUMPTION_BOUND');
    for (const [b, tier] of breaks) {
      if (b <= tS) continue;
      const [l, r] = splitAt(rest, b);
      if (l.length > 1) segs.push([l, cur]);
      rest = r;
      cur = tier;
    }
    if (rest.length > 1) segs.push([rest, cur]);
    const col = colorOf(t);
    const w = WIDTH[t.detail] || 1.6;
    const picked = o.picked.includes(t.thread_id);
    segs.forEach(([seg, tier], i) => {
      const s = TIER_STYLE[tier] || TIER_STYLE.B_ASSUMPTION_BOUND;
      els.push(
        <Path
          key={`th${t.thread_id}:${i}`}
          d={d(seg)}
          fill="none"
          stroke={col}
          strokeWidth={o.focus === t.thread_id || picked ? w + 1 : w}
          strokeOpacity={s.op * dimOp(t.thread_id)}
          strokeDasharray={s.dash}
          strokeLinecap="round"
          strokeLinejoin="round"
        />,
      );
    });
    els.push(
      <Path
        key={`hit${t.thread_id}`}
        d={d(pts)}
        fill="none"
        stroke={c.bg}
        strokeOpacity={0.01}
        strokeWidth={18}
        {...press(`hit:${t.thread_id}`, () => onThread(t.thread_id))}
      />,
    );
  }
  /* moments (beads) — shared history is drawn once, on the parent */
  for (const t of drawOrder) {
    if (!L.lines[t.thread_id]) continue;
    const big = o.focus === t.thread_id || !!o.compare;
    for (const m of t.moments) {
      if (m.kind === 'start' || m.kind === 'end') continue;
      if (t.parent && m.i < t.split_index) continue;
      const tm = ms(m.t);
      const cc = L.crossAt(t, tm);
      if (cc === null) continue;
      const [x, y] = P(tm, cc);
      const r = big ? 5 : 3.6;
      const col = colorOf(t);
      let shape: React.ReactNode;
      if (m.kind === 'action') shape = <Circle cx={x} cy={y} r={r} fill={c.bg} stroke={person(people[m.actor || ''] ?? 0)} strokeWidth={big ? 2.4 : 1.8} />;
      else if (m.kind === 'agreement')
        shape = <Path d={`M${x},${y - r - 1.5} L${x + r + 1.5},${y} L${x},${y + r + 1.5} L${x - r - 1.5},${y} Z`} fill={col} stroke={c.bg} strokeWidth={1.5} />;
      else if (m.kind === 'threshold')
        shape = <Path d={`M${x},${y - r - 1} L${x + r + 1},${y + r} L${x - r - 1},${y + r} Z`} fill={c.warn} stroke={c.bg} strokeWidth={1.2} />;
      else if (m.kind === 'external')
        shape = (
          <Path
            d={`M${x},${y - r - 2} L${x + 1.6},${y - 1.6} L${x + r + 2},${y} L${x + 1.6},${y + 1.6} L${x},${y + r + 2} L${x - 1.6},${y + 1.6} L${x - r - 2},${y} L${x - 1.6},${y - 1.6} Z`}
            fill={c.present}
          />
        );
      else if (m.kind === 'blocked')
        shape = <Path d={`M${x - r},${y - r} L${x + r},${y + r} M${x + r},${y - r} L${x - r},${y + r}`} stroke={c.cut} strokeWidth={2.2} />;
      else shape = <Circle cx={x} cy={y} r={r - 0.8} fill={c.bg} stroke={c.muted} strokeWidth={1.4} />;
      els.push(
        <G key={`b${t.thread_id}:${m.i}`} opacity={dimOp(t.thread_id)} {...press(`bead:${t.thread_id}:${m.i}`, () => onBead(t, m))}>
          {shape}
          {/* Invisible tap target. Opacity 0, not 0.01: on iOS stacked near-transparent circles showed as grey halos.
           * Hit-testing uses the shape, not the paint, so it still receives taps (iOS, Android and the web). */}
          <Circle cx={x} cy={y} r={11} fill={c.bg} fillOpacity={0} />
        </G>,
      );
    }
  }
  /* nexus nodes */
  Object.values(L.nexus).forEach((nx) => {
    const pulse = !!o.compare || (!!o.focus && nx.kids.some((t) => t.thread_id === o.focus));
    els.push(
      <G key={`nx${nx.key}`} {...press(`nexus:${nx.key}`, () => onNexus(nx))}>
        <Circle cx={nx.x} cy={nx.y} r={nx.kids.length > 1 ? 6 : 5} fill={c.bg} stroke={c.present} strokeWidth={pulse ? 3 : 2} />
        <Circle cx={nx.x} cy={nx.y} r={13} fill={c.bg} fillOpacity={0} />
      </G>,
    );
  });
  /* ends + numbered keys */
  for (const t of L.threads) {
    const pts = L.lines[t.thread_id];
    if (!pts) continue;
    const [tt, cc] = pts[pts.length - 1];
    const [x, y] = P(tt, cc);
    const col = colorOf(t);
    const op = dimOp(t.thread_id);
    let glyph: React.ReactNode;
    if (t.horizon_reached) glyph = <Circle cx={x} cy={y} r={5.5} fill={c.bg} stroke={col} strokeWidth={2.4} />;
    else if (t.stopped_early)
      glyph = (
        <G>
          {[1, 2, 3].map((i) => {
            const [dx, dy] = vertical ? [0, i * 6] : [i * 6, 0];
            return <Circle key={i} cx={x + dx} cy={y + dy} r={1.7} fill={col} opacity={1 - i * 0.25} />;
          })}
        </G>
      );
    else glyph = <Rect x={x - 5} y={y - 5} width={10} height={10} rx={1.5} fill={col} />;
    const [kx, ky] = vertical ? [x, y + 20] : [x + 18, y];
    els.push(
      <G key={`end${t.thread_id}`} opacity={op} {...press(`end:${t.thread_id}`, () => onThread(t.thread_id))}>
        {glyph}
        <Circle cx={kx} cy={ky} r={9} fill={col} />
        <SvgText x={kx} y={ky + 3.5} textAnchor="middle" fill={isDark ? '#0b0d12' : '#fffdf8'} fontSize={10.5} fontWeight="bold">
          {String(keyNo[t.thread_id])}
        </SvgText>
        {L.wideLabels && !metric ? (
          <SvgText x={kx + 14} y={ky + 4} fill={c.text2} fontSize={12}>
            {t.outcome_label.length > 24 ? t.outcome_label.slice(0, 23) + '…' : t.outcome_label}
            {t.horizon_reached ? null : <TSpan fill={c.muted}>{` · ${fmtTime(t.ended_at, res)}`}</TSpan>}
          </SvgText>
        ) : null}
      </G>,
    );
  }
  /* time cursor */
  if (cursor !== null) {
    const [x, y] = P(cursor, 0);
    els.push(
      vertical ? (
        <Line key="cur" x1={mL - 6} y1={y} x2={W - 10} y2={y} stroke={c.text} strokeWidth={1.2} strokeDasharray="3 3" />
      ) : (
        <Line key="cur" x1={x} y1={mT - 6} x2={x} y2={mT + crossLen + 4} stroke={c.text} strokeWidth={1.2} strokeDasharray="3 3" />
      ),
    );
  }

  return (
    <Animated.View style={{ opacity: fade }}>
      <View accessible accessibilityRole="image" accessibilityLabel={summary} {...webClick}>
        <Svg width={W} height={H}>
          {els}
        </Svg>
      </View>
    </Animated.View>
  );
}

/** Legend for the map's visual grammar. */
export function MapLegend() {
  const { c } = useTheme();
  const item = (svg: React.ReactNode, label: string) => (
    <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <Svg width={26} height={14}>
        {svg}
      </Svg>
      <T v="tiny">{label}</T>
    </View>
  );
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8 }} accessibilityLabel="How to read the map">
      {item(<Line x1={2} y1={7} x2={24} y2={7} stroke={c.text2} strokeWidth={2.4} />, 'grounded / stated assumptions')}
      {item(<Line x1={2} y1={7} x2={24} y2={7} stroke={c.text2} strokeWidth={2.4} strokeDasharray="7 4" />, 'illustrative (frayed)')}
      {item(<Circle cx={13} cy={7} r={4.5} fill={c.bg} stroke={c.present} strokeWidth={2} />, 'paths split')}
      {item(<Circle cx={13} cy={7} r={4.5} fill="none" stroke={c.text2} strokeWidth={2} />, 'reached horizon')}
      {item(<Rect x={8.5} y={2.5} width={9} height={9} fill={c.text2} />, 'ended')}
      {item(
        <G>
          <Circle cx={6} cy={7} r={1.6} fill={c.muted} />
          <Circle cx={13} cy={7} r={1.6} fill={c.muted} />
          <Circle cx={20} cy={7} r={1.6} fill={c.muted} />
        </G>,
        'stopped early',
      )}
      {item(<Line x1={2} y1={7} x2={24} y2={7} stroke={c.ghost} strokeWidth={1.6} strokeDasharray="2 5" />, 'identified, not simulated')}
    </View>
  );
}
