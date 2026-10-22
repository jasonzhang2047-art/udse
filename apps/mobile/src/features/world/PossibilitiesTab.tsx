/* Possibilities — the coverage frontier (GET /v1/decisions/{id}/coverage, docs/design/ui-redesign.md §5.8).
 * Grouped by status in plain words; simulated ones link to their paths on the map. Never exhaustive. */

import { useEffect } from 'react';
import { Pressable, View } from 'react-native';

import { Banner, Card, ErrorBox, Loading, OfflineNote, Pill, Row, T } from '@/components/ui';
import { humanize } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { Coverage, CoverageItem, Futures } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const ORDER: [string, string, string][] = [
  ['DEPTH_SIMULATED', '◉', 'On the map, simulated in detail'],
  ['LOW_FIDELITY_EXPLORED', '◎', 'Previewed — rough, exploratory only'],
  ['REOPENED_AFTER_REVISION', '↺', 'Reopened after new evidence'],
  ['DISCOVERED_UNSIMULATED', '┄', 'Identified, not simulated yet'],
  ['SET_ASIDE_BUDGET', '⏸', 'Set aside by budget (still possible)'],
  ['REJECTED_VALIDATED_CONSTRAINT', '✂', 'Ruled out by an evidenced rule'],
];

function reason(i: CoverageItem): string {
  const r = i.status_reason || '';
  if (/^\d+ detailed traject/.test(r) || /^\d+ screening run/.test(r)) return '';
  return r
    .replace(/requires unmodeled mechanism\(s\): /, 'needs a model of ')
    .replace(/not feasible under constraint ([^:]+):/, 'not feasible — $1:')
    .replace(/_/g, ' ');
}

export function PossibilitiesTab({
  id,
  refresh,
  goTab,
}: {
  id: string;
  refresh: number;
  goTab: (k: 'futures', extra?: Record<string, string | undefined>) => void;
}) {
  const { c, outcome } = useTheme();
  const cov = useApi<Coverage>(`/v1/decisions/${id}/coverage`);
  const fut = useApi<Futures>(`/v1/decisions/${id}/futures?light=true`);
  useEffect(() => {
    if (refresh) {
      cov.reload();
      fut.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  if (cov.error) return <ErrorBox error={cov.error} onRetry={cov.reload} />;
  if (!cov.data) return <Loading label="Loading possibilities" />;
  const pathOf: Record<string, number> = {};
  const colorOf: Record<string, string> = {};
  if (fut.data) {
    const fam = Object.fromEntries(fut.data.families.map((f) => [f.key, f.color_index]));
    fut.data.threads.forEach((t, n) =>
      t.run_ids.forEach((r) => {
        pathOf[r] = n + 1;
        colorOf[r] = outcome(fam[t.family] ?? 0);
      }),
    );
  }
  const groups: Record<string, CoverageItem[]> = {};
  cov.data.items.forEach((i) => (groups[i.status] = groups[i.status] || []).push(i));

  return (
    <View>
      <OfflineNote cachedAt={cov.cachedAt} />
      <Banner tone="warn">{cov.data.not_yet_discovered_warning || 'This list is not exhaustive. Possibilities may exist that nobody has identified.'}</Banner>
      <T v="small" color={c.muted}>
        Searched with {cov.data.discovery_methods.length} independent lens{cov.data.discovery_methods.length === 1 ? '' : 'es'}:{' '}
        {cov.data.discovery_methods.map(humanize).join(', ').toLowerCase()}.
      </T>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 12 }}>
        {ORDER.filter(([s]) => groups[s]).map(([s, g, title]) => (
          <Card key={s} style={{ flexGrow: 1, flexBasis: 300 }}>
            <Row between>
              <T v="h3" style={{ flex: 1 }} color={s === 'REJECTED_VALIDATED_CONSTRAINT' ? c.cut : c.text}>
                {g} {title}
              </T>
              <Pill>{String(groups[s].length)}</Pill>
            </Row>
            {groups[s].map((i) => {
              const seen = new Set<number>();
              const chips = i.run_ids
                .filter((r) => pathOf[r] && !seen.has(pathOf[r]) && (seen.add(pathOf[r]), true))
                .map((r) => (
                  <Pressable
                    key={r}
                    accessibilityRole="link"
                    accessibilityLabel={`Open path ${pathOf[r]} on the map`}
                    onPress={() => goTab('futures', { run: r })}
                    style={{ width: 26, height: 26, borderRadius: 13, backgroundColor: colorOf[r], alignItems: 'center', justifyContent: 'center' }}
                  >
                    <T v="tiny" color={c.onOutcome} style={{ fontWeight: '700' }} allowFontScaling={false}>
                      {pathOf[r]}
                    </T>
                  </Pressable>
                ));
              return (
                <View key={i.id} style={{ paddingVertical: 8, borderTopWidth: 1, borderTopColor: c.line }}>
                  <T v="small" color={c.text} style={{ fontWeight: '600' }}>
                    {i.label}
                  </T>
                  {reason(i) ? <T v="tiny">{reason(i)}</T> : null}
                  {i.citation.length ? <T v="mono">cites {i.citation.join(', ')}</T> : null}
                  {chips.length ? (
                    <Row gap={6} style={{ marginTop: 6 }}>
                      <T v="tiny">{chips.length === 1 ? 'path' : 'paths'}</T>
                      {chips}
                    </Row>
                  ) : null}
                </View>
              );
            })}
          </Card>
        ))}
      </View>
      {cov.data.stop_rationale ? (
        <T v="small" color={c.muted} style={{ marginTop: 6 }}>
          {cov.data.stop_rationale}
        </T>
      ) : null}
      {cov.data.suspected_blind_spots.length ? (
        <>
          <T v="h2">Suspected blind spots</T>
          <Card>
            {cov.data.suspected_blind_spots.map((x) => (
              <T key={x} v="small">
                • {x}
              </T>
            ))}
          </Card>
        </>
      ) : null}
    </View>
  );
}
