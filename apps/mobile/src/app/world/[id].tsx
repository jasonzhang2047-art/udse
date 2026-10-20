/* A decision ("world"): header, owner actions as async jobs, and the five sections of
 * docs/design/ui-redesign.md §4 — Futures · Evidence · World model · Possibilities · Setup. */

import { router, Stack, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { Avatar, Banner, Button, ErrorBox, Loading, OfflineNote, Pill, Row, Screen, Segmented, T } from '@/components/ui';
import { EvidenceTab } from '@/features/world/EvidenceTab';
import { FuturesTab } from '@/features/world/FuturesTab';
import { ModelTab } from '@/features/world/ModelTab';
import { PossibilitiesTab } from '@/features/world/PossibilitiesTab';
import { SetupTab } from '@/features/world/SetupTab';
import { ApiError, api, waitJob } from '@/lib/api';
import { fmtTime, humanize } from '@/lib/format';
import { useSession } from '@/lib/session';
import { useTheme } from '@/lib/theme';
import type { DecisionDetail, Job, JobRef, ModelSummary } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const TABS = [
  { key: 'futures', label: 'Futures' },
  { key: 'evidence', label: 'Evidence' },
  { key: 'model', label: 'World model' },
  { key: 'possibilities', label: 'Possibilities' },
  { key: 'setup', label: 'Setup' },
] as const;
type TabKey = (typeof TABS)[number]['key'];

export default function World() {
  const { c } = useTheme();
  const { me } = useSession();
  const params = useLocalSearchParams<{ id: string; tab?: string; v?: string; run?: string }>();
  const id = params.id;
  const tab: TabKey = (TABS.find((t) => t.key === params.tab)?.key as TabKey) || 'futures';
  const d = useApi<DecisionDetail>(`/v1/decisions/${id}`);
  const models = useApi<ModelSummary[]>(`/v1/decisions/${id}/models`);
  const [refresh, setRefresh] = useState(0);
  const [job, setJob] = useState<{ label: string; status?: string; error?: string; done?: string } | null>(null);

  const goTab = (k: TabKey, extra: Record<string, string | undefined> = {}) => router.setParams({ tab: k, v: undefined, run: undefined, ...extra });

  if (d.error) {
    const denied = d.error instanceof ApiError && (d.error.status === 403 || d.error.status === 404);
    return (
      <Screen>
        {denied ? (
          <Banner tone="warn">
            <T v="h3">This world isn't shared with {me?.display_name || 'you'}</T>
            <T v="small" style={{ marginTop: 6 }}>
              Each decision is visible only to its participants. Switch participant or go back to your worlds.
            </T>
            <Row style={{ marginTop: 10 }}>
              <Button small label="Your worlds" onPress={() => router.replace('/')} />
              <Button small kind="ghost" label="Switch participant" onPress={() => router.push('/login')} />
            </Row>
          </Banner>
        ) : (
          <ErrorBox error={d.error} onRetry={d.reload} />
        )}
      </Screen>
    );
  }
  if (!d.data || !models.data) {
    return (
      <Screen>
        <Loading label="Opening this world" />
        {models.error ? <ErrorBox error={models.error} onRetry={models.reload} /> : null}
      </Screen>
    );
  }

  const { spec, scope, role } = d.data;
  const latest = models.data[models.data.length - 1] || null;
  const owner = role === 'owner';
  const offline = d.cachedAt || models.cachedAt;
  const running = !!job && !job.error && !job.done;

  const runJob = async (label: string, start: () => Promise<JobRef>, then: TabKey, doneLabel: (r: unknown) => string) => {
    setJob({ label });
    try {
      const result = await waitJob(await start(), (j: Job) => setJob({ label, status: j.status }));
      setJob({ label, done: doneLabel(result) });
      models.reload();
      d.reload();
      setRefresh((r) => r + 1);
      goTab(then);
    } catch (e) {
      setJob({ label, error: (e as Error).message });
    }
  };
  const build = () =>
    runJob(
      'Researching → building → checking the world model',
      () => api.post<JobRef>(`/v1/decisions/${id}/models/compile`, {}),
      'model',
      (r) => {
        const s = (r as { status?: string })?.status || '';
        return s === 'MODEL_READY' ? 'World model ready.' : `World model: ${humanize(s.toLowerCase())}.`;
      },
    );
  const explore = () =>
    runJob(
      'Exploring futures: exploratory previews, sensitivity probes, then detailed simulations',
      async () => {
        const plan = await api.post<{ experiment_id: string }>(`/v1/decisions/${id}/experiments`, {});
        return api.post<JobRef>(`/v1/experiments/${plan.experiment_id}/runs`, {});
      },
      'futures',
      () => 'New futures explored.',
    );

  return (
    <Screen onRefresh={() => (d.reload(), models.reload(), setRefresh((r) => r + 1))} refreshing={d.loading && !!d.data}>
      <Stack.Screen options={{ title: spec.question.length > 38 ? spec.question.slice(0, 36) + '…' : spec.question }} />
      <OfflineNote cachedAt={offline} />
      <T v="display" accessibilityRole="header">
        {spec.question}
      </T>
      <Row style={{ marginTop: 10 }}>
        <Pill>{`${fmtTime(spec.horizon.starts_at, scope.time_resolution, true)} → ${fmtTime(spec.horizon.ends_at, scope.time_resolution, true)}`}</Pill>
        <Row gap={0}>
          {spec.participants.map((p, i) => (
            <View key={p.agent_id} style={{ marginLeft: i ? -6 : 0 }}>
              <Avatar name={p.display_name || p.agent_id} index={i} size={26} />
            </View>
          ))}
        </Row>
        {latest ? (
          <Button
            small
            kind="ghost"
            label={`World model v${latest.version} · ${latest.passed ? 'checked' : 'failed checks'}`}
            accessibilityLabel={`World model version ${latest.version}, ${latest.passed ? 'passed its checks' : 'failed its checks'}. Open World model.`}
            onPress={() => goTab('model')}
          />
        ) : (
          <Pill tone="warn">No world model yet</Pill>
        )}
      </Row>
      {owner ? (
        <Row style={{ marginTop: 10 }}>
          <Button small label={latest ? 'Rebuild model' : 'Build world model'} onPress={build} disabled={running || !!offline} />
          <Button
            small
            kind="primary"
            label="Explore futures"
            onPress={explore}
            disabled={running || !!offline || !latest || !latest.allowed_run_modes.length}
          />
        </Row>
      ) : (
        <T v="tiny" style={{ marginTop: 8 }}>
          Only the owner can rebuild the model or explore more futures.
        </T>
      )}
      {job ? (
        <View style={{ marginTop: 10 }} accessibilityLiveRegion="polite">
          {job.error ? (
            <ErrorBox error={`${job.label} failed: ${job.error}`} />
          ) : job.done ? (
            <Banner tone="ok">{job.done}</Banner>
          ) : (
            <Row>
              <Loading label={`${job.label}${job.status ? ` — ${job.status.toLowerCase()}` : ''}`} />
            </Row>
          )}
        </View>
      ) : null}
      {scope.high_stakes ? (
        <Banner tone="bad" style={{ marginTop: 10 }}>
          {`High-stakes decision (${scope.high_stakes_reasons.join('; ')}): numbers from unverified mechanisms are withheld. Use this for questions to ask, not answers.`}
        </Banner>
      ) : null}
      <View style={{ marginTop: 14, marginBottom: 12, borderBottomWidth: 1, borderBottomColor: c.line, paddingBottom: 10 }}>
        <Segmented label="World sections" options={[...TABS]} value={tab} onChange={(k) => goTab(k)} />
      </View>
      {tab === 'futures' ? (
        <FuturesTab id={id} latest={latest} version={params.v ? Number(params.v) : null} focusRun={params.run || null} refresh={refresh} goTab={goTab} />
      ) : tab === 'evidence' ? (
        <EvidenceTab id={id} refresh={refresh} />
      ) : tab === 'model' ? (
        <ModelTab
          id={id}
          models={models.data}
          version={params.v ? Number(params.v) : null}
          refresh={refresh}
          onVersion={(v) => router.setParams({ v: String(v) })}
        />
      ) : tab === 'possibilities' ? (
        <PossibilitiesTab id={id} refresh={refresh} goTab={goTab} />
      ) : (
        <SetupTab spec={spec} scope={scope} />
      )}
    </Screen>
  );
}
