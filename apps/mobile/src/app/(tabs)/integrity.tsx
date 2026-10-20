/* Integrity — the boundary log (GET /v1/audit?denied_only=true, docs/design/ui-redesign.md §5.10):
 * every refused write across real evidence, world models, simulated futures and your profile. */

import { View } from 'react-native';

import { Card, Empty, ErrorBox, Loading, OfflineNote, Row, Screen, T } from '@/components/ui';
import { humanize } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { AuditRow } from '@/lib/types';
import { useApi } from '@/lib/useApi';

type Zone = 'evidence' | 'model' | 'sim' | 'profile' | 'other';
const zoneOf = (target: string): Zone =>
  /claims|curated|registry|raw_source|evidence/.test(target)
    ? 'evidence'
    : /profile|human_evidence/.test(target)
      ? 'profile'
      : /model_versions/.test(target)
        ? 'model'
        : /simulation|run_/.test(target)
          ? 'sim'
          : 'other';

export default function Integrity() {
  const { c, outcome } = useTheme();
  const rows = useApi<AuditRow[]>('/v1/audit?denied_only=true');
  const counts: Record<Zone, number> = { evidence: 0, model: 0, sim: 0, profile: 0, other: 0 };
  (rows.data || []).forEach((r) => counts[zoneOf(r.target)]++);
  const zones: [Zone, string, string, string][] = [
    ['evidence', 'Real evidence', 'Observations, official rules, datasets — written only by the evidence pipeline.', c.ground],
    ['model', 'World models', 'Versioned, immutable; each run pins one exact model.', outcome(4)],
    ['sim', 'Simulated futures', 'Append-only event logs; can only queue research.', c.simInk],
    ['profile', 'Your profile', 'Changes only from your own approved evidence.', c.present],
  ];

  return (
    <Screen onRefresh={rows.reload} refreshing={rows.loading && !!rows.data}>
      <T v="eyebrow">Integrity</T>
      <T v="display" accessibilityRole="header" style={{ marginTop: 6 }}>
        Simulated futures can never become facts.
      </T>
      <T v="lede" style={{ marginTop: 8, marginBottom: 12 }}>
        Four zones with separate write authority. Every attempt to cross a line the wrong way is refused and logged here — including attempts disguised by
        rewording.
      </T>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        {zones.map(([k, title, sub, col]) => (
          <View
            key={k}
            style={{ flexGrow: 1, flexBasis: 160, borderWidth: 1, borderColor: col + '66', backgroundColor: col + '12', borderRadius: 14, padding: 12 }}
          >
            <T v="eyebrow" color={col}>
              {title}
            </T>
            <T v="tiny" style={{ marginTop: 4 }}>
              {sub}
            </T>
            <T v="display" style={{ marginTop: 6 }}>
              {String(counts[k])}
            </T>
            <T v="tiny">refused writes into this zone</T>
          </View>
        ))}
      </View>
      <T v="h2">Refused crossings</T>
      <OfflineNote cachedAt={rows.cachedAt} />
      {rows.error ? <ErrorBox error={rows.error} onRetry={rows.reload} /> : null}
      {!rows.data && !rows.error ? <Loading label="Loading the boundary log" /> : null}
      {rows.data && !rows.data.length ? (
        <Empty>Nothing refused yet. Open any moment on a Futures map and choose “Try to treat it as real evidence”.</Empty>
      ) : null}
      {rows.data?.length ? (
        <Card>
          {rows.data.map((r, i) => (
            <View key={r.id} style={{ paddingVertical: 8, borderTopWidth: i ? 1 : 0, borderTopColor: c.line }}>
              <Row between>
                <T v="mono">{r.at.slice(5, 19).replace('T', ' ')}</T>
                <T v="tiny">
                  {humanize(r.role)} · {r.operation}
                </T>
              </Row>
              <T v="small" color={c.text}>
                {r.reason}
              </T>
              <T v="mono">{r.target}</T>
            </View>
          ))}
        </Card>
      ) : null}
    </Screen>
  );
}
