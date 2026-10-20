/* Worlds (Decision Home): GET /v1/decisions, plus a light /futures view per world for its thumbnail. */

import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';

import { MiniMap } from '@/components/MiniMap';
import { Avatar, Button, Empty, ErrorBox, Loading, OfflineNote, Pill, Row, Screen, T } from '@/components/ui';
import { getCached } from '@/lib/api';
import { fmtTime } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { DecisionSummary, Futures } from '@/lib/types';
import { useApi } from '@/lib/useApi';

export default function Worlds() {
  const { c } = useTheme();
  const ds = useApi<DecisionSummary[]>('/v1/decisions');
  const [futures, setFutures] = useState<Record<string, Futures | null>>({});

  useEffect(() => {
    if (!ds.data) return;
    let alive = true;
    ds.data
      .filter((d) => d.model_version)
      .forEach((d) =>
        getCached<Futures>(`/v1/decisions/${d.decision_id}/futures?light=true`)
          .then((r) => alive && setFutures((f) => ({ ...f, [d.decision_id]: r.data })))
          .catch(() => alive && setFutures((f) => ({ ...f, [d.decision_id]: null }))),
      );
    return () => {
      alive = false;
    };
  }, [ds.data]);

  return (
    <Screen onRefresh={ds.reload} refreshing={ds.loading && !!ds.data}>
      <T v="eyebrow">Your worlds</T>
      <T v="display" accessibilityRole="header" style={{ marginTop: 6 }}>
        Every decision is a small world with many possible futures.
      </T>
      <T v="lede" style={{ marginTop: 8 }}>
        The engine builds only the world your decision needs, from evidence it can cite, then lets futures branch over time. It never treats a simulated event
        as a fact.
      </T>
      <Button kind="primary" label="Ask a new question" onPress={() => router.push('/new')} style={{ marginVertical: 14 }} />
      <OfflineNote cachedAt={ds.cachedAt} />
      {ds.loading && !ds.data ? <Loading label="Loading your worlds" /> : null}
      {ds.error ? <ErrorBox error={ds.error} onRetry={ds.reload} /> : null}
      {ds.data && !ds.data.length ? <Empty>No worlds yet. Ask a question to begin.</Empty> : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
        {ds.data?.map((d) => {
          const f = futures[d.decision_id];
          return (
            <Pressable
              key={d.decision_id}
              accessibilityRole="link"
              accessibilityLabel={`${d.question}. Open this world.`}
              onPress={() => router.push({ pathname: '/world/[id]', params: { id: d.decision_id } })}
              style={({ pressed }) => ({
                flexGrow: 1,
                flexBasis: 320,
                backgroundColor: pressed ? c.surface2 : c.surface,
                borderColor: c.line,
                borderWidth: 1,
                borderRadius: 16,
                padding: 14,
                gap: 10,
              })}
            >
              <View style={{ backgroundColor: c.bg2, borderRadius: 10, overflow: 'hidden', borderWidth: 1, borderColor: c.line }}>
                <MiniMap data={d.model_version ? (f ?? null) : null} />
              </View>
              <T v="h1" style={{ fontSize: 20, lineHeight: 26 }}>
                {d.question}
              </T>
              <Row between>
                <T v="small" color={c.muted}>
                  {fmtTime(d.horizon.starts_at, 'month', true)} → {fmtTime(d.horizon.ends_at, 'month', true)}
                </T>
                <Row gap={0}>
                  {d.participants.slice(0, 5).map((p, i) => (
                    <View key={p} style={{ marginLeft: i ? -6 : 0 }}>
                      <Avatar name={p} index={i} size={24} />
                    </View>
                  ))}
                </Row>
              </Row>
              {d.model_version ? (
                <Row gap={16}>
                  <Stat n={f ? f.threads.length : d.runs} label={f ? 'futures explored' : 'runs'} />
                  {f ? <Stat n={f.ghosts_total} label="not yet simulated" /> : null}
                  {f ? <Stat n={f.evidence.gaps.length} label="evidence gaps" /> : null}
                </Row>
              ) : (
                <Pill tone="warn">No world model yet</Pill>
              )}
              {d.role === 'owner' ? <T v="tiny">You own this world</T> : null}
            </Pressable>
          );
        })}
      </View>
      <T v="tiny" style={{ marginTop: 14 }}>
        Counts are coverage of what was explored — never odds.
      </T>
    </Screen>
  );
}

function Stat({ n, label }: { n: number; label: string }) {
  return (
    <View>
      <T v="h1" style={{ fontSize: 22, lineHeight: 26 }}>
        {n}
      </T>
      <T v="tiny">{label}</T>
    </View>
  );
}
