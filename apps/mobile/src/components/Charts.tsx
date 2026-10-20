/* Step charts of committed points only: values between events are never interpolated, and time after a
 * run stopped early is grayed out rather than extrapolated. */

import { useState } from 'react';
import { View } from 'react-native';
import Svg, { Circle, Line, Path, Pattern, Rect, Defs, Text as SvgText } from 'react-native-svg';

import { fmtNum, fmtTime, ms } from '@/lib/format';
import { useTheme } from '@/lib/theme';

export interface StepPoint {
  t: number;
  v: number;
}

export function StepChart({
  points,
  start,
  end,
  lastSimulated,
  stoppedEarly,
  cursor,
  unit,
  res,
  color,
  label,
}: {
  points: StepPoint[];
  start: string;
  end: string;
  lastSimulated: string;
  stoppedEarly: boolean;
  cursor: number | null;
  unit: string | null;
  res: string;
  color: string;
  label: string;
}) {
  const { c } = useTheme();
  const [W, setW] = useState(320);
  const H = 190;
  const P = { l: 12, r: 12, t: 26, b: 28 };
  const t0 = ms(start);
  const t1 = ms(end);
  const tl = Math.min(ms(lastSimulated), t1);
  const X = (t: number) => P.l + ((Math.min(Math.max(t, t0), t1) - t0) / Math.max(1, t1 - t0)) * (W - P.l - P.r);
  const vals = points.map((p) => p.v);
  const lo = Math.min(0, ...vals);
  let hi = Math.max(...vals, lo);
  if (hi === lo) hi = lo + 1;
  const Y = (v: number) => H - P.b - ((v - lo) / (hi - lo)) * (H - P.t - P.b);
  let d = '';
  points.forEach((p, i) => {
    d += i === 0 ? `M${X(p.t)},${Y(p.v)}` : ` H${X(p.t)} V${Y(p.v)}`;
  });
  if (points.length) d += ` H${X(tl)}`;
  const gx = X(tl);
  const summary = points.length
    ? `${label}: ${points.length} committed values from ${fmtNum(points[0].v, unit)} to ${fmtNum(points[points.length - 1].v, unit)}${stoppedEarly ? '; not simulated after ' + fmtTime(lastSimulated, res, true) : ''}. Simulated.`
    : `${label}: no values`;

  return (
    <View onLayout={(e) => setW(Math.max(240, e.nativeEvent.layout.width))} accessible accessibilityRole="image" accessibilityLabel={summary}>
      <Svg width={W} height={H}>
        <Defs>
          <Pattern id="hatch" patternUnits="userSpaceOnUse" width={6} height={6} patternTransform="rotate(45)">
            <Line x1={0} y1={0} x2={0} y2={6} stroke={c.line2} strokeWidth={2} />
          </Pattern>
        </Defs>
        {stoppedEarly && tl < t1 ? (
          <>
            <Rect x={gx} y={P.t - 8} width={W - P.r - gx} height={H - P.t - P.b + 8} fill="url(#hatch)" opacity={0.7} />
            <SvgText x={Math.min(gx + 6, W - 90)} y={P.t + 6} fill={c.muted} fontSize={11}>
              not simulated
            </SvgText>
          </>
        ) : null}
        <Line x1={P.l} y1={H - P.b} x2={W - P.r} y2={H - P.b} stroke={c.line2} strokeWidth={1} />
        <SvgText x={P.l} y={14} fill={c.muted} fontSize={11}>
          {fmtNum(hi, unit)}
        </SvgText>
        <SvgText x={P.l} y={H - P.b - 4} fill={c.muted} fontSize={11}>
          {fmtNum(lo, unit)}
        </SvgText>
        <Path d={d} fill="none" stroke={color} strokeWidth={2.2} />
        {points.map((p, i) => (
          <Circle key={i} cx={X(p.t)} cy={Y(p.v)} r={2.6} fill={color} />
        ))}
        {cursor !== null ? <Line x1={X(cursor)} y1={P.t - 10} x2={X(cursor)} y2={H - P.b} stroke={c.present} strokeWidth={1.5} strokeDasharray="4 3" /> : null}
        <SvgText x={P.l} y={H - 8} fill={c.muted} fontSize={11}>
          {fmtTime(start, res, true)}
        </SvgText>
        <SvgText x={W - P.r} y={H - 8} fill={c.muted} fontSize={11} textAnchor="end">
          {fmtTime(end, res, true)}
        </SvgText>
      </Svg>
    </View>
  );
}

/** Tiny step sparkline for the story panel (committed points only). */
export function Sparkline({ series, end, color }: { series: [string, number][] | undefined; end: string; color: string }) {
  if (!series || series.length < 2) return null;
  const w = 110;
  const h = 28;
  const t0 = ms(series[0][0]);
  const t1 = Math.max(ms(end), ms(series[series.length - 1][0]));
  const vs = series.map((p) => p[1]);
  const lo = Math.min(...vs, 0);
  const hi = Math.max(...vs, lo + 1e-9);
  const X = (t: string) => ((ms(t) - t0) / Math.max(1, t1 - t0)) * w;
  const Y = (v: number) => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
  let d = `M${X(series[0][0])},${Y(series[0][1])}`;
  for (let i = 1; i < series.length; i++) d += ` H${X(series[i][0])} V${Y(series[i][1])}`;
  d += ` H${w}`;
  return (
    <View aria-hidden>
      <Svg width={w} height={h}>
        <Path d={d} fill="none" stroke={color} strokeWidth={1.6} />
      </Svg>
    </View>
  );
}
