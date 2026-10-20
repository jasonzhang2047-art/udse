/* Static futures thumbnail for world cards: present line, threads, end glyphs. No labels. */

import { View } from 'react-native';
import Svg, { Circle, Line, Path, Rect, Text as SvgText } from 'react-native-svg';

import { ms } from '@/lib/format';
import { laneOrder, visibleThreads } from '@/lib/futures';
import { useTheme } from '@/lib/theme';
import type { Futures } from '@/lib/types';

export function MiniMap({ data }: { data: Futures | null }) {
  const { c, outcome } = useTheme();
  const W = 320;
  const H = 92;
  if (!data || !data.threads.length) {
    return (
      <View aria-hidden>
        <Svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H}>
          <Line x1={14} y1={10} x2={14} y2={82} stroke={c.present} strokeWidth={2} />
          <SvgText x={24} y={50} fill={c.muted} fontSize={11}>
            {data ? 'No futures explored yet' : ''}
          </SvgText>
        </Svg>
      </View>
    );
  }
  const t0 = ms(data.horizon.start);
  const t1 = ms(data.horizon.end);
  const threads = data.threads.filter((t) => t.detail !== 'probe');
  const vis = visibleThreads({ ...data, threads }, { showPreviews: true, showProbes: false });
  const order = laneOrder(vis);
  const gap = Math.min(16, (H - 16) / Math.max(order.length, 1));
  const lane: Record<string, number> = Object.fromEntries(order.map((t, i) => [t.thread_id, 8 + (i + 0.5) * gap + (H - 16 - order.length * gap) / 2]));
  const fam = Object.fromEntries(data.families.map((f) => [f.key, f.color_index]));
  const X = (t: number) => 14 + ((Math.min(Math.max(t, t0), t1) - t0) / Math.max(1, t1 - t0)) * (W - 30);
  return (
    <View aria-hidden>
      <Svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        <Line x1={14} y1={6} x2={14} y2={H - 6} stroke={c.present} strokeWidth={2} opacity={0.9} />
        {vis.map((t) => {
          const y = lane[t.thread_id];
          const col = outcome(fam[t.family] ?? 0);
          const xe = X(ms(t.ended_at));
          let d: string;
          if (!t.parent) d = `M14,${y} H${xe}`;
          else {
            const xs = X(ms(t.split_at));
            const y0 = lane[t.parent];
            d = `M${xs},${y0} C${xs + 10},${y0} ${xs + 10},${y} ${xs + 20},${y} H${Math.max(xe, xs + 20)}`;
          }
          const ex = Math.max(xe, 30);
          return [
            <Path
              key={t.thread_id}
              d={d}
              fill="none"
              stroke={col}
              strokeWidth={t.detail === 'detailed' ? 2 : 1.2}
              strokeOpacity={t.detail === 'detailed' ? 1 : 0.6}
            />,
            t.horizon_reached ? (
              <Circle key={t.thread_id + 'e'} cx={ex} cy={y} r={3} fill={c.bg2} stroke={col} strokeWidth={1.6} />
            ) : (
              <Rect key={t.thread_id + 'e'} x={ex - 3} y={y - 3} width={6} height={6} fill={col} />
            ),
          ];
        })}
      </Svg>
    </View>
  );
}
