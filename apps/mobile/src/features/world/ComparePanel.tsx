/* Compare two threads (docs/design/ui-redesign.md §5.5): shared history, the first difference on each side,
 * what was changed on purpose, and where they end up as paired bars. */

import { router } from 'expo-router';
import { View } from 'react-native';

import { Banner, Button, Card, Dot, Pill, Row, SimStamp, T } from '@/components/ui';
import { fmtNum, fmtTime, ms } from '@/lib/format';
import { valueAt } from '@/lib/futures';
import { useTheme } from '@/lib/theme';
import type { Compare, Futures, Thread } from '@/lib/types';

export function ComparePanel({
  data,
  a: A,
  b: B,
  cmp,
  keyNo,
  colorOf,
  onClose,
  bare,
}: {
  data: Futures;
  a: Thread;
  b: Thread;
  cmp: Compare;
  keyNo: Record<string, number>;
  colorOf: (t: { family: string }) => string;
  onClose: () => void;
  /** Rendered inside a sheet that has its own title and close button. */
  bare?: boolean;
}) {
  const { c } = useTheme();
  const res = data.horizon.resolution;
  const prefix = cmp.common_prefix_events;
  const firstAfter = (t: Thread) => t.moments.find((m) => m.i >= prefix && m.kind !== 'start');
  const side = (t: Thread) => {
    const f = firstAfter(t);
    return (
      <View style={{ flex: 1, minWidth: 220, borderWidth: 1, borderColor: c.line, borderRadius: 12, padding: 10 }}>
        <Row gap={6} wrap={false}>
          <Dot color={colorOf(t)} />
          <T v="small" color={c.text} style={{ fontWeight: '600', flex: 1 }}>
            {keyNo[t.thread_id]}. {t.label}
          </T>
        </Row>
        <T v="small" style={{ marginTop: 4 }}>
          {f ? f.headline : 'no further events'}
        </T>
        <T v="small" color={colorOf(t)} style={{ marginTop: 4 }}>
          → {t.outcome_label}
        </T>
        <Row style={{ marginTop: 6 }}>
          {(t.carries.length ? t.carries : ['No extra assumptions']).map((x) => (
            <Pill key={x} tone="sim">
              {x}
            </Pill>
          ))}
        </Row>
      </View>
    );
  };

  return (
    <Card style={bare ? { borderWidth: 0, padding: 0, backgroundColor: 'transparent' } : undefined}>
      {bare ? null : (
        <Row between>
          <T v="eyebrow">Compare</T>
          <Button small kind="ghost" label="Close" onPress={onClose} />
        </Row>
      )}
      <Row between style={{ marginTop: 4 }}>
        <T v="h1" accessibilityRole="header">
          Where two futures part
        </T>
        <SimStamp />
      </Row>
      <View style={{ marginTop: 10 }}>
        {cmp.banner ? (
          <Banner tone="warn">{cmp.banner}</Banner>
        ) : (
          <Banner tone="sim">Same world model and the same random draws: inside this model, differences come from what was changed on purpose.</Banner>
        )}
      </View>
      <T v="small" color={c.text}>
        {prefix ? `Shared history: ${prefix} events` : 'They differ from the very start'}
        {cmp.first_divergence.a && prefix ? `, then they part at ${fmtTime(cmp.first_divergence.a.sim_at, res, true)}` : ''}.
      </T>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 10 }}>
        {side(A)}
        {side(B)}
      </View>
      {cmp.intervention.policy_id ? (
        <T v="small" style={{ marginTop: 10 }}>
          Different decision policy: {A.label.split(' — ')[0]} vs {B.label.split(' — ')[0]}
        </T>
      ) : null}
      {data.metrics.length ? (
        <>
          <T v="h3" style={{ marginTop: 14 }}>
            Where they end up
          </T>
          {data.metrics.map((m) => {
            const va = valueAt(A.series[m.id], ms(A.ended_at));
            const vb = valueAt(B.series[m.id], ms(B.ended_at));
            const hi = Math.max(Math.abs(va || 0), Math.abs(vb || 0), 1e-9);
            const bar = (v: number | null, t: Thread) => (
              <Row wrap={false} gap={8}>
                <View style={{ flex: 1, height: 8, backgroundColor: c.surface2, borderRadius: 4 }}>
                  <View style={{ width: `${Math.max(2, (Math.abs(v || 0) / hi) * 100)}%`, height: 8, borderRadius: 4, backgroundColor: colorOf(t) }} />
                </View>
                <T v="tiny" style={{ width: 90, textAlign: 'right' }}>
                  {fmtNum(v, m.unit)}
                </T>
              </Row>
            );
            return (
              <View
                key={m.id}
                style={{ marginTop: 8 }}
                accessible
                accessibilityLabel={`${m.label}: path ${keyNo[A.thread_id]} ${fmtNum(va, m.unit)}, path ${keyNo[B.thread_id]} ${fmtNum(vb, m.unit)}. Simulated.`}
              >
                <T v="small">{m.label}</T>
                {bar(va, A)}
                {bar(vb, B)}
              </View>
            );
          })}
        </>
      ) : null}
      {cmp.limitations[0] ? (
        <T v="tiny" style={{ marginTop: 10 }}>
          {cmp.limitations[0]}
        </T>
      ) : null}
      <Button
        label="Open full divergence view →"
        onPress={() => router.push({ pathname: '/compare/[a]/[b]', params: { a: A.rep_run_id, b: B.rep_run_id } })}
        style={{ marginTop: 12 }}
      />
    </Card>
  );
}
