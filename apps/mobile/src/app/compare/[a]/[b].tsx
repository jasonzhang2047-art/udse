/* Divergence view (GET /v1/runs/{a}/compare/{b}): where two simulated runs part, what differed by design,
 * and end-state metrics side by side. Inside one model and the same random draws only. */

import { router, Stack, useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';

import { Banner, Button, Card, ErrorBox, KV, Loading, OfflineNote, Pill, Row, Screen, SimStamp, T } from '@/components/ui';
import { fmtTime, fmtVal, humanize, outcomeWords, resolutionFor, statusWords } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { Compare, RunDetail, Timeline } from '@/lib/types';
import { useApi } from '@/lib/useApi';

export default function Divergence() {
  const { c, outcome } = useTheme();
  const { a, b } = useLocalSearchParams<{ a: string; b: string }>();
  const cmp = useApi<Compare>(`/v1/runs/${a}/compare/${b}`);
  const ra = useApi<RunDetail>(`/v1/runs/${a}`);
  const rb = useApi<RunDetail>(`/v1/runs/${b}`);
  const ta = useApi<Timeline>(`/v1/runs/${a}/timeline`);

  if (cmp.error)
    return (
      <Screen>
        <ErrorBox error={cmp.error} onRetry={cmp.reload} />
      </Screen>
    );
  if (!cmp.data || !ra.data || !rb.data)
    return (
      <Screen>
        <Loading label="Comparing" />
      </Screen>
    );
  const d = cmp.data;
  const meta = ta.data?.metric_meta || {};
  const res = ta.data ? resolutionFor(ta.data.horizon_start, ta.data.horizon_end) : 'minute';
  const side = (key: 'a' | 'b', r: RunDetail, color: string) => {
    const fd = d.first_divergence[key];
    const out = d.outcomes[key];
    const status = d.outcomes[`${key}_status` as 'a_status' | 'b_status'];
    return (
      <Card style={{ flexGrow: 1, flexBasis: 280, borderColor: color + '88' }}>
        <Row between>
          <T v="eyebrow" color={color}>
            {key.toUpperCase()}
          </T>
          <SimStamp />
        </Row>
        <T v="h3" style={{ marginTop: 4 }}>
          {r.manifest.label}
        </T>
        <Row style={{ marginTop: 6 }}>
          <Pill>{statusWords(status)}</Pill>
          {out ? <Pill color={color}>{outcomeWords(out)}</Pill> : null}
        </Row>
        {fd ? (
          <T v="small" style={{ marginTop: 8 }}>
            First differing event ({fmtTime(fd.sim_at, res, true)}): {fd.text}
          </T>
        ) : (
          <T v="small" color={c.muted} style={{ marginTop: 8 }}>
            No differing event on this side.
          </T>
        )}
        <Button
          small
          label="Open timeline →"
          onPress={() => router.push({ pathname: '/run/[id]', params: { id: r.manifest.run_id } })}
          style={{ marginTop: 10 }}
        />
      </Card>
    );
  };

  return (
    <Screen onRefresh={cmp.reload} refreshing={cmp.loading}>
      <Stack.Screen options={{ title: 'Divergence' }} />
      <OfflineNote cachedAt={cmp.cachedAt} />
      <T v="display" accessibilityRole="header">
        Where two futures part
      </T>
      <View style={{ marginTop: 10 }}>
        {d.banner ? (
          <Banner tone="warn">{d.banner}</Banner>
        ) : (
          <Banner tone="sim">
            Same world model and the same exogenous random draws: inside this model, differences are attributable to what was changed on purpose.
          </Banner>
        )}
      </View>
      <Card>
        <T v="h3">Changed on purpose</T>
        {Object.keys(d.intervention).length ? (
          <KV rows={Object.entries(d.intervention).map(([k, v]) => [humanize(k), `A: ${fmtVal(v.a as never)} · B: ${fmtVal(v.b as never)}`])} />
        ) : (
          <T v="small" color={c.muted}>
            No manifest difference.
          </T>
        )}
        <T v="small" style={{ marginTop: 8 }}>
          {d.common_prefix_events ? `Shared committed events before they part: ${d.common_prefix_events}` : 'They differ from the very first event.'}
        </T>
        {d.common_ancestor_state_hash ? (
          <T v="mono" numberOfLines={1}>
            common ancestor state {d.common_ancestor_state_hash}
          </T>
        ) : null}
      </Card>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
        {side('a', ra.data, outcome(0))}
        {side('b', rb.data, outcome(1))}
      </View>
      {d.counterfactual ? (
        <Card>
          <T v="h3">Counterfactual</T>
          <T v="small">{d.counterfactual}</T>
        </Card>
      ) : null}
      <T v="h2">Where they end up</T>
      <Card>
        <Row between style={{ marginBottom: 4 }}>
          <T v="tiny">Metric</T>
          <T v="tiny">A · B (simulated)</T>
        </Row>
        <KV
          rows={Object.entries(d.metrics).map(([k, v]) => [
            meta[k]?.label || humanize(k),
            `${v.a?.value !== undefined ? fmtVal(v.a.value) : humanize((v.a?.status || '—').toLowerCase())} · ${v.b?.value !== undefined ? fmtVal(v.b.value) : humanize((v.b?.status || '—').toLowerCase())}`,
          ])}
        />
      </Card>
      <T v="h2">Limitations</T>
      {d.limitations.map((l) => (
        <T key={l} v="small" style={{ marginBottom: 4 }}>
          • {l}
        </T>
      ))}
    </Screen>
  );
}
