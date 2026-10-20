/* Timeline explorer for one run: GET /v1/runs/{id}/timeline, /state?seq=, /why/{seq} and
 * /events/{seq}/promotion-check. A scrubber walks committed events; the step chart shows committed points
 * only, and time after a run stopped early is grayed out rather than extrapolated. */

import { router, Stack, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, View } from 'react-native';

import { EventActions } from '@/components/Causes';
import { StepChart, type StepPoint } from '@/components/Charts';
import { Reaction } from '@/components/Reaction';
import { Scrubber } from '@/components/Scrubber';
import { Banner, Button, Card, ErrorBox, KV, Loading, OfflineNote, Pill, Row, Screen, Segmented, Sheet, SimStamp, T, TierMeter } from '@/components/ui';
import { getCached } from '@/lib/api';
import { fmtTime, fmtVal, humanize, isQuantity, ms, outcomeWords, resolutionFor, statusWords } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { RunCard, RunDetail, StateAt, Timeline, TimelineEvent } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const FILTERS = [
  { key: 'all', label: 'All events' },
  { key: 'agents', label: 'Agents' },
  { key: 'world', label: 'World processes' },
  { key: 'info', label: 'Information' },
] as const;
type Filter = (typeof FILTERS)[number]['key'];

const MODE_WORD: Record<string, string> = { screening: 'Exploratory preview', depth: 'Detailed simulation', sensitivity: 'Sensitivity probe' };

const keep = (f: Filter, e: TimelineEvent) =>
  f === 'all' ||
  (f === 'agents' && e.kind === 'AGENT_DECISION') ||
  (f === 'world' && ['MECHANISM_TICK', 'GATE_FIRE', 'GATE_RESET', 'EXOGENOUS', 'CONSTRAINT_BLOCKED', 'POLICY_INIT', 'QUIESCENT_COLLAPSE'].includes(e.kind)) ||
  (f === 'info' && e.kind === 'DOMAIN_EVENT');

export default function RunTimeline() {
  const { c, outcome } = useTheme();
  const { id, seq: seqParam } = useLocalSearchParams<{ id: string; seq?: string }>();
  const tl = useApi<Timeline>(`/v1/runs/${id}/timeline`);
  const run = useApi<RunDetail>(`/v1/runs/${id}`);
  const [sel, setSel] = useState<number | null>(null);
  const [metric, setMetric] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>('all');
  const [state, setState] = useState<{ data: StateAt | null; error: Error | null; loading: boolean }>({ data: null, error: null, loading: false });
  const [open, setOpen] = useState<TimelineEvent | null>(null);
  const [picker, setPicker] = useState(false);
  const stateReq = useRef(0);

  const events = tl.data?.events || [];
  const res = tl.data ? resolutionFor(tl.data.horizon_start, tl.data.horizon_end) : 'minute';
  const numeric = useMemo(() => {
    if (!tl.data) return [];
    return Object.entries(tl.data.metric_meta).filter(([k, m]) => m.computable && (tl.data!.series[k] || []).some((p) => isQuantity(p.value)));
  }, [tl.data]);
  const withheld = tl.data ? Object.entries(tl.data.metric_meta).filter(([, m]) => !m.computable) : [];

  // Start at the requested event, else the last one.
  useEffect(() => {
    if (!tl.data || sel !== null) return;
    const want = seqParam ? events.findIndex((e) => e.seq === Number(seqParam)) : -1;
    setSel(want >= 0 ? want : Math.max(0, events.length - 1));
    if (!metric && numeric.length) setMetric(numeric[0][0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tl.data]);

  const loadState = (idx: number) => {
    const e = events[idx];
    if (!e) return;
    const req = ++stateReq.current;
    setState((s) => ({ ...s, loading: true, error: null }));
    getCached<StateAt>(`/v1/runs/${id}/state?seq=${e.seq}`)
      .then((r) => req === stateReq.current && setState({ data: r.data, error: null, loading: false }))
      .catch((err: Error) => req === stateReq.current && setState({ data: null, error: err, loading: false }));
  };
  useEffect(() => {
    if (sel !== null) loadState(sel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel === null]);

  if (tl.error)
    return (
      <Screen>
        <ErrorBox error={tl.error} onRetry={tl.reload} />
      </Screen>
    );
  if (!tl.data || sel === null)
    return (
      <Screen>
        <Loading label="Loading the timeline" />
      </Screen>
    );
  const t = tl.data;
  const cur = events[sel];
  const stopped = !t.horizon_reached && !!t.grayed_after;
  const points: StepPoint[] = metric
    ? (t.series[metric] || []).filter((p) => isQuantity(p.value)).map((p) => ({ t: ms(p.sim_at), v: (p.value as { value: number }).value }))
    : [];
  const meta = metric ? t.metric_meta[metric] : null;
  const shown = events.filter((e) => keep(filter, e) && !(e.kind === 'MECHANISM_TICK' && !e.changes.length));
  const decisionId = run.data?.manifest.decision_id;

  return (
    <Screen onRefresh={tl.reload} refreshing={tl.loading}>
      <Stack.Screen options={{ title: t.label || t.policy_id }} />
      <OfflineNote cachedAt={tl.cachedAt} />
      {decisionId ? (
        <Button
          small
          kind="ghost"
          label="← Back to this world's futures"
          onPress={() => router.push({ pathname: '/world/[id]', params: { id: decisionId, run: id } })}
        />
      ) : null}
      <T v="display" accessibilityRole="header" style={{ marginTop: 6 }}>
        {t.label || t.policy_id}
      </T>
      <Row style={{ marginTop: 8 }}>
        <Pill tone={t.status === 'BUDGET_EXHAUSTED' ? 'warn' : t.status === 'FAILED' || t.status === 'MODEL_EXECUTION_BLOCKED' ? 'bad' : 'default'}>
          {statusWords(t.status)}
        </Pill>
        {t.terminal_outcome ? <Pill>{outcomeWords(t.terminal_outcome)}</Pill> : null}
        <Pill>{MODE_WORD[t.fidelity_mode] || humanize(t.fidelity_mode)}</Pill>
        {t.deadline_disclosure !== 'NOT_APPLICABLE' ? <Pill>{humanize(t.deadline_disclosure.toLowerCase())}</Pill> : null}
        <SimStamp />
      </Row>
      {t.reason ? (
        <T v="small" color={c.muted} style={{ marginTop: 6 }}>
          {t.reason}
        </T>
      ) : null}
      {stopped ? (
        <Banner tone="warn" style={{ marginTop: 10 }}>
          {`Stopped early at ${fmtTime(t.last_simulated_at, res, true)} — the horizon was not reached. Later periods are grayed out and never fabricated.${t.grayed_reason ? ' ' + t.grayed_reason : ''}`}
        </Banner>
      ) : null}
      {t.outcome_flags.length ? (
        <Row style={{ marginTop: 8 }}>
          {t.outcome_flags.map((f) => (
            <Pill key={f} tone="warn">
              {humanize(f.toLowerCase())}
            </Pill>
          ))}
        </Row>
      ) : null}

      <Card style={{ marginTop: 12 }}>
        {numeric.length ? (
          <>
            <Segmented label="Metric" options={numeric.map(([k, m]) => ({ key: k, label: m.label }))} value={metric || numeric[0][0]} onChange={setMetric} />
            <View style={{ marginTop: 8 }}>
              <StepChart
                points={points}
                start={t.horizon_start}
                end={t.horizon_end}
                lastSimulated={t.last_simulated_at}
                stoppedEarly={!t.horizon_reached}
                cursor={cur ? ms(cur.sim_at) : null}
                unit={meta?.unit ?? null}
                res={res}
                color={outcome(4)}
                label={meta?.label || ''}
              />
            </View>
          </>
        ) : (
          <T v="small" color={c.muted}>
            No numeric metric to chart.
          </T>
        )}
        <T v="tiny">{t.resolution_note}</T>
        {withheld.map(([k, m]) => (
          <T key={k} v="tiny" color={c.bad}>
            {m.label}: withheld — {m.reason}
          </T>
        ))}

        <T v="small" style={{ marginTop: 12 }}>
          Scrub through committed events
        </T>
        <Scrubber
          value={sel}
          min={0}
          max={Math.max(0, events.length - 1)}
          steps={Math.max(1, events.length - 1)}
          onChange={(v) => setSel(Math.round(v))}
          onCommit={(v) => loadState(Math.round(v))}
          label="Simulated time, by committed event"
          valueText={cur ? `Event ${sel + 1} of ${events.length}, ${fmtTime(cur.sim_at, res, true)}: ${cur.summary}` : ''}
        />
        {cur ? (
          <View style={{ marginTop: 6 }} accessibilityLiveRegion="polite">
            <T v="mono" color={c.text}>
              Event {sel + 1}/{events.length} · {fmtTime(cur.sim_at, res, true)}
            </T>
            <T v="small" color={c.text}>
              {cur.summary}
            </T>
          </View>
        ) : null}

        <View style={{ marginTop: 12, borderTopWidth: 1, borderTopColor: c.line, paddingTop: 10 }}>
          <Row between>
            <T v="h3">State after event {cur?.seq}</T>
            <SimStamp />
          </Row>
          {state.loading ? <Loading label="Reconstructing state" /> : null}
          {state.error ? <ErrorBox error={state.error} onRetry={() => loadState(sel)} /> : null}
          {state.data ? (
            <KV
              rows={Object.entries(state.data.world)
                .filter(([, v]) => v.visibility !== 'hidden')
                .slice(0, 40)
                .map(([k, v]) => [
                  <Row key={k} gap={6} wrap={false}>
                    {v.tier ? <TierMeter tier={v.tier} /> : null}
                    <T v="small" color={c.muted} style={{ flex: 1 }}>
                      {v.label}
                    </T>
                  </Row>,
                  fmtVal(v.value, v.unit),
                ])}
            />
          ) : null}
        </View>
      </Card>

      <View style={{ marginVertical: 8 }}>
        <Segmented label="Event filter" options={[...FILTERS]} value={filter} onChange={setFilter} />
      </View>
      <View accessibilityRole="list">
        {shown.map((e) => {
          const idx = events.indexOf(e);
          const after = idx > sel;
          const isSel = idx === sel;
          return (
            <Pressable
              key={e.seq}
              accessibilityRole="button"
              accessibilityLabel={`${fmtTime(e.sim_at, res, true)}: ${e.summary}. Simulated.${after ? ' After the scrubber position.' : ''}`}
              onPress={() => setOpen(e)}
              style={({ pressed }) => ({
                flexDirection: 'row',
                gap: 10,
                paddingVertical: 8,
                paddingHorizontal: 10,
                borderRadius: 10,
                borderLeftWidth: 3,
                borderLeftColor: isSel ? c.present : 'transparent',
                backgroundColor: pressed || isSel ? c.surface2 : 'transparent',
                opacity: after ? 0.5 : 1,
              })}
            >
              <T v="mono" style={{ width: 92 }}>
                {fmtTime(e.sim_at, res, true)}
              </T>
              <View style={{ flex: 1 }}>
                <T v="small" color={c.text}>
                  {e.summary}
                </T>
                <Row gap={6}>
                  {e.rejection_code ? <Pill tone="bad">rejected</Pill> : null}
                  {e.tier && e.tier !== 'A_GROUNDED' ? <TierMeter tier={e.tier} /> : null}
                </Row>
                {e.rationale ? (
                  <T v="tiny">
                    “{e.rationale.slice(0, 160)}
                    {e.rationale.length > 160 ? '…' : ''}”
                  </T>
                ) : null}
              </View>
            </Pressable>
          );
        })}
      </View>

      <T v="h2">Compare</T>
      <Button label="Compare with another path…" onPress={() => setPicker(true)} disabled={!decisionId} />

      <T v="h2">Your reaction (real human evidence)</T>
      {decisionId ? <Reaction decisionId={decisionId} runIds={[id]} pathLabel={t.label} /> : <Loading />}

      <Sheet visible={!!open} onClose={() => setOpen(null)} title={open?.summary || 'Event'}>
        {open ? <EventSheet e={open} runId={id} res={res} /> : null}
      </Sheet>
      <Sheet visible={picker} onClose={() => setPicker(false)} title="Compare with…">
        {picker && decisionId ? (
          <RunPicker
            decisionId={decisionId}
            exclude={id}
            onPick={(b) => (setPicker(false), router.push({ pathname: '/compare/[a]/[b]', params: { a: id, b } }))}
          />
        ) : null}
      </Sheet>
    </Screen>
  );
}

function EventSheet({ e, runId, res }: { e: TimelineEvent; runId: string; res: string }) {
  const { c } = useTheme();
  return (
    <View>
      <Row>
        <SimStamp />
        <Pill>{humanize(e.kind.toLowerCase())}</Pill>
        {e.tier ? <TierMeter tier={e.tier} withWords /> : null}
        <Pill>{`seq ${e.seq}`}</Pill>
        <Pill>{fmtTime(e.sim_at, res, true)}</Pill>
      </Row>
      {e.actor_name ? (
        <T v="small" color={c.muted} style={{ marginTop: 6 }}>
          {e.actor_name}'s Agent (simulated — not the real person)
        </T>
      ) : null}
      {e.rationale ? (
        <>
          <T v="h3" style={{ marginTop: 12 }}>
            Agent's stated rationale (simulated)
          </T>
          <T v="small">{e.rationale}</T>
        </>
      ) : null}
      {e.uncertainty ? <T v="tiny">Uncertainty: {e.uncertainty}</T> : null}
      {e.policy_kind ? <T v="tiny">Policy: {e.policy_kind}</T> : null}
      {e.rejection_code ? <Banner tone="bad">{`Rejected: ${humanize(e.rejection_code.toLowerCase())}`}</Banner> : null}
      {e.changes.length ? (
        <>
          <T v="h3" style={{ marginTop: 12 }}>
            State changes
          </T>
          <KV rows={e.changes.map((ch) => [ch.ref, `${fmtVal(ch.before)} → ${fmtVal(ch.after)}`])} />
        </>
      ) : null}
      {e.draws.length ? (
        <>
          <T v="h3" style={{ marginTop: 12 }}>
            Random draws (recorded)
          </T>
          {e.draws.map((d, i) => (
            <T key={i} v="mono">
              {d.stream} · {JSON.stringify(d.value)}
            </T>
          ))}
        </>
      ) : null}
      {e.assumptions.length ? (
        <>
          <T v="h3" style={{ marginTop: 12 }}>
            Assumptions
          </T>
          {e.assumptions.map((a) => (
            <T key={a} v="small">
              • {a}
            </T>
          ))}
        </>
      ) : null}
      <EventActions runId={runId} seq={e.seq} res={res} />
    </View>
  );
}

function RunPicker({ decisionId, exclude, onPick }: { decisionId: string; exclude: string; onPick: (runId: string) => void }) {
  const { c } = useTheme();
  const runs = useApi<RunCard[]>(`/v1/decisions/${decisionId}/runs`);
  if (runs.error) return <ErrorBox error={runs.error} onRetry={runs.reload} />;
  if (!runs.data) return <Loading />;
  return (
    <View style={{ gap: 6 }}>
      {runs.data
        .filter((r) => r.run_id !== exclude)
        .map((r) => (
          <Pressable
            key={r.run_id}
            accessibilityRole="button"
            onPress={() => onPick(r.run_id)}
            style={({ pressed }) => ({
              padding: 10,
              borderRadius: 10,
              borderWidth: 1,
              borderColor: c.line,
              backgroundColor: pressed ? c.surface2 : 'transparent',
            })}
          >
            <T v="small" color={c.text} style={{ fontWeight: '600' }}>
              {r.name}
            </T>
            <T v="tiny">
              {MODE_WORD[r.fidelity_mode || ''] || r.fidelity_mode} · {statusWords(r.status)}
            </T>
          </Pressable>
        ))}
    </View>
  );
}
