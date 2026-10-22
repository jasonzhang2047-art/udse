/* Futures (docs/design/ui-redesign.md §5.3–5.5): the futures map, a time cursor, the path list (the map's
 * accessible equivalent), the thread story or comparison, unexplored possibilities, and every run. */

import { router, useIsFocused } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { Pressable, Switch, useWindowDimensions, View } from 'react-native';

import { EventActions } from '@/components/Causes';
import { FuturesMap, MapLegend, type MapOptions, type Nexus } from '@/components/FuturesMap';
import { Scrubber } from '@/components/Scrubber';
import { Banner, Button, Card, Dot, Empty, ErrorBox, Loading, OfflineNote, Pill, Row, Segmented, Sheet, SimStamp, T, TierMeter } from '@/components/ui';
import { api } from '@/lib/api';
import { DETAIL_WORD, fmtNum, fmtTime, humanize, ms, simTimeAt, statusWords } from '@/lib/format';
import { valueAt, visibleThreads, type PlacedThread } from '@/lib/futures';
import { useTheme } from '@/lib/theme';
import type { Compare, Futures, Ghost, ModelSummary, Moment, RunCard, Thread } from '@/lib/types';
import { useApi } from '@/lib/useApi';

import { ComparePanel } from './ComparePanel';
import { StoryPanel } from './StoryPanel';

type GoTab = (k: 'futures' | 'evidence' | 'model' | 'possibilities' | 'setup', extra?: Record<string, string | undefined>) => void;

export function FuturesTab({
  id,
  latest,
  version,
  focusRun,
  refresh,
  goTab,
}: {
  id: string;
  latest: ModelSummary | null;
  version: number | null;
  focusRun: string | null;
  refresh: number;
  goTab: GoTab;
}) {
  const f = useApi<Futures>(latest ? `/v1/decisions/${id}/futures${version ? `?version=${version}` : ''}` : null);
  const runs = useApi<RunCard[]>(latest ? `/v1/decisions/${id}/runs` : null);
  useEffect(() => {
    if (refresh) {
      f.reload();
      runs.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  if (!latest) return <Empty>Build a world model first — it decides what can be simulated responsibly.</Empty>;
  if (f.error) return <ErrorBox error={f.error} onRetry={f.reload} />;
  if (!f.data) return <Loading label="Drawing the futures map" />;
  return (
    <View>
      <OfflineNote cachedAt={f.cachedAt} />
      <FuturesView key={`${f.data.model.version}`} data={f.data} decisionId={id} focusRun={focusRun} goTab={goTab} runs={runs.data} />
    </View>
  );
}

function FuturesView({
  data,
  decisionId,
  focusRun,
  goTab,
  runs,
}: {
  data: Futures;
  decisionId: string;
  focusRun: string | null;
  goTab: GoTab;
  runs: RunCard[] | null;
}) {
  const { c, outcome } = useTheme();
  const { width: winW } = useWindowDimensions();
  // Sheets hide while another screen is on top (e.g. after "Open timeline" from inside a sheet).
  const focused = useIsFocused();
  // Seeded from the window (screen gutters + card padding) so the map paints on the first frame;
  // onLayout then corrects it to the card's real width.
  const [mapW, setMapW] = useState(() => Math.max(280, Math.min(winW, 1120) - 52));
  const narrow = winW < 640;
  const [o, setO] = useState<MapOptions>(() => ({
    lens: 'structure',
    showPreviews: !narrow,
    showProbes: false,
    showGhosts: true,
    focus: (focusRun && data.threads.find((t) => t.run_ids.includes(focusRun))?.thread_id) || null,
    compare: null,
    picked: [],
  }));
  const [picking, setPicking] = useState(false);
  const [cursor, setCursor] = useState<number | null>(null);
  const [beads, setBeads] = useState<{ t: PlacedThread; m: Moment } | null>(null);
  const [nexus, setNexus] = useState<Nexus | null>(null);
  const [ghost, setGhost] = useState<Ghost | null>(null);
  const [cmpErr, setCmpErr] = useState<string | null>(null);

  const res = data.horizon.resolution;
  const t0 = ms(data.horizon.start);
  const t1 = ms(data.horizon.end);
  const fam = Object.fromEntries(data.families.map((x) => [x.key, x.color_index]));
  const colorOf = (t: { family: string }) => outcome(fam[t.family] ?? 0);
  const keyNo = useMemo(() => Object.fromEntries(data.threads.map((t, i) => [t.thread_id, i + 1])), [data]);
  const byId = useMemo(() => Object.fromEntries(data.threads.map((t) => [t.thread_id, t])), [data]);
  const versions = data.model.versions;
  const wide = winW >= 1000;
  const story = o.compare || o.focus;

  const set = (patch: Partial<MapOptions>) => setO((p) => ({ ...p, ...patch }));

  const pickOrFocus = async (tid: string) => {
    if (!picking) {
      set({ focus: o.focus === tid ? null : tid });
      return;
    }
    const picked = o.picked.includes(tid) ? o.picked : [...o.picked, tid];
    if (picked.length < 2) {
      set({ picked });
      return;
    }
    setPicking(false);
    await compareTwo(picked[0], picked[1]);
  };

  /** Divergence of two threads' representative runs (GET /v1/runs/{a}/compare/{b}). */
  const compareTwo = async (a: string, b: string) => {
    setCmpErr(null);
    try {
      const cmp = await api.get<Compare>(`/v1/runs/${byId[a].rep_run_id}/compare/${byId[b].rep_run_id}`);
      // Same render as the comparison, so a split sheet turns into the comparison instead of closing first.
      setNexus(null);
      set({ compare: { a, b, cmp }, picked: [], focus: null });
    } catch (e) {
      setNexus(null);
      setCmpErr((e as Error).message);
      set({ picked: [] });
    }
  };

  /* One sheet for the story, moments, splits and possibilities. iOS can't present a modal while another is
   * being dismissed, so moving from one view to the next swaps this sheet's content instead. */
  const sheet = !focused ? null : beads ? 'moment' : nexus ? 'nexus' : ghost ? 'ghost' : !wide && story ? 'story' : null;
  const closeSheet = () => {
    if (sheet === 'moment') setBeads(null);
    else if (sheet === 'nexus') setNexus(null);
    else if (sheet === 'ghost') setGhost(null);
    else set({ focus: null, compare: null });
  };
  const sheetTitle =
    sheet === 'moment'
      ? 'Moment'
      : sheet === 'nexus'
        ? 'What happened differently here'
        : sheet === 'ghost'
          ? ghost?.label || 'Possibility'
          : o.compare
            ? 'Compare two paths'
            : o.focus
              ? `Path ${keyNo[o.focus]}`
              : '';

  /* readout at the time cursor */
  const visible = useMemo(() => visibleThreads(data, o), [data, o]);
  const metric = o.lens !== 'structure' ? data.metrics.find((m) => m.id === o.lens) : null;
  const readout =
    cursor === null
      ? []
      : visible
          .map((th) => {
            const ended = cursor > ms(th.ended_at);
            let val: string;
            let v = 0;
            if (metric) {
              const x = valueAt(th.series[metric.id], cursor);
              v = x ?? -Infinity;
              val = ended ? 'ended' : fmtNum(x, metric.unit);
            } else {
              const past = th.moments.filter((m) => ms(m.t) <= cursor && m.kind !== 'start');
              val = ended ? th.outcome_label : past.pop()?.headline || 'under way';
            }
            return { th, val, v };
          })
          .sort((a, b) => (metric ? b.v - a.v : 0));

  const mapCard = (
    <Card style={{ padding: 10 }}>
      <View onLayout={(e) => setMapW(Math.floor(e.nativeEvent.layout.width))}>
        {mapW > 0 ? (
          <FuturesMap
            data={data}
            o={o}
            width={mapW}
            cursor={cursor}
            keyNo={keyNo}
            onThread={pickOrFocus}
            onBead={(t, m) => setBeads({ t, m })}
            onNexus={setNexus}
            onGhost={setGhost}
          />
        ) : null}
      </View>
      <View style={{ paddingHorizontal: 4 }}>
        <T v="small" style={{ marginTop: 6 }}>
          Time cursor
        </T>
        <Scrubber
          value={cursor ?? t0}
          min={t0}
          max={t1}
          onChange={setCursor}
          label="Time cursor on the futures map"
          valueText={cursor === null ? 'not set' : simTimeAt(cursor, data.horizon.start, res)}
        />
        {cursor !== null ? (
          <View style={{ marginTop: 6 }} accessibilityLiveRegion="polite">
            <Row between>
              <T v="mono" color={c.text}>
                {simTimeAt(cursor, data.horizon.start, res)}
              </T>
              <Row gap={6}>
                <SimStamp />
                <Button small kind="ghost" label="Clear" onPress={() => setCursor(null)} />
              </Row>
            </Row>
            {readout.slice(0, 9).map(({ th, val }) => (
              <Row key={th.thread_id} between wrap={false} style={{ paddingVertical: 2 }}>
                <Row gap={6} wrap={false} style={{ flex: 1 }}>
                  <Dot color={colorOf(th)} />
                  <T v="small" numberOfLines={1} style={{ flex: 1 }}>
                    {keyNo[th.thread_id]}. {th.label}
                  </T>
                </Row>
                <T v="small" color={c.text} numberOfLines={1} style={{ maxWidth: '50%', textAlign: 'right' }}>
                  {val}
                </T>
              </Row>
            ))}
          </View>
        ) : (
          <T v="tiny">Drag or step the cursor to read every visible path at one moment.</T>
        )}
        <MapLegend />
        <T v="tiny" style={{ marginTop: 6 }}>
          Above the amber line: what is known now. Beyond it: simulation only. {data.note}
        </T>
      </View>
    </Card>
  );

  const storyPanel = o.compare ? (
    <ComparePanel
      data={data}
      a={byId[o.compare.a]}
      b={byId[o.compare.b]}
      cmp={o.compare.cmp!}
      keyNo={keyNo}
      colorOf={colorOf}
      bare={!wide}
      onClose={() => set({ compare: null })}
    />
  ) : o.focus && byId[o.focus] ? (
    <StoryPanel
      data={data}
      t={byId[o.focus]}
      keyNo={keyNo}
      colorOf={colorOf}
      cursor={cursor}
      decisionId={decisionId}
      bare={!wide}
      onClose={() => set({ focus: null })}
    />
  ) : null;

  return (
    <View>
      {versions.length > 1 ? (
        <View style={{ marginBottom: 10 }}>
          <T v="eyebrow">World model</T>
          <View style={{ marginTop: 6 }}>
            <Segmented
              label="Model version"
              options={versions.map((v) => ({ key: String(v), label: `v${v} futures` }))}
              value={String(data.model.version)}
              onChange={(v) => goTab('futures', { v })}
            />
          </View>
          <T v="tiny" style={{ marginTop: 4 }}>
            Futures from different model versions are never mixed on one map.
          </T>
        </View>
      ) : null}

      <KnownNow data={data} onOpen={() => goTab('evidence')} />

      {!data.threads.length ? (
        <Empty>{`No futures explored on model v${data.model.version} yet. Use “Explore futures” to let paths branch.`}</Empty>
      ) : (
        <>
          <View style={{ gap: 8, marginBottom: 10 }}>
            <Segmented
              label="Map lens"
              options={[{ key: 'structure', label: 'Shape of futures' }, ...data.metrics.map((m) => ({ key: m.id, label: m.label }))]}
              value={o.lens}
              onChange={(lens) => set({ lens })}
            />
            <Row gap={14}>
              <Toggle label="Previews" value={o.showPreviews} onChange={(v) => set({ showPreviews: v })} />
              <Toggle label="Sensitivity probes" value={o.showProbes} onChange={(v) => set({ showProbes: v })} />
              {!narrow ? <Toggle label="Unexplored" value={o.showGhosts} onChange={(v) => set({ showGhosts: v })} /> : null}
              {o.compare ? (
                <Button small label="Exit compare" onPress={() => set({ compare: null })} />
              ) : (
                <Button
                  small
                  kind={picking ? 'primary' : 'default'}
                  label={picking ? `Pick ${2 - o.picked.length} path${o.picked.length === 1 ? '' : 's'}… (cancel)` : 'Compare two paths'}
                  onPress={() => {
                    setPicking(!picking);
                    set({ picked: [] });
                  }}
                />
              )}
            </Row>
            {picking ? <Banner tone="sim">Tap two paths on the map or in the list below to compare them.</Banner> : null}
            {cmpErr ? <ErrorBox error={cmpErr} /> : null}
          </View>

          {wide && story ? (
            <View style={{ flexDirection: 'row', gap: 14, alignItems: 'flex-start' }}>
              <View style={{ flex: 1, minWidth: 0 }}>{mapCard}</View>
              <View style={{ width: 380 }}>{storyPanel}</View>
            </View>
          ) : (
            mapCard
          )}
          {!o.compare ? (
            <Card>
              <Row between>
                <T v="h3">Paths</T>
                <T v="tiny">{data.threads.length} distinct simulated paths · counts are coverage, never odds</T>
              </Row>
              <View style={{ marginTop: 8, gap: 6 }}>
                {data.threads.map((t) => (
                  <PathRow
                    key={t.thread_id}
                    t={t}
                    n={keyNo[t.thread_id]}
                    color={colorOf(t)}
                    res={res}
                    hidden={
                      !(
                        t === data.threads[0] ||
                        t.detail === 'detailed' ||
                        (t.detail === 'preview' && o.showPreviews) ||
                        (t.detail === 'probe' && o.showProbes)
                      )
                    }
                    selected={o.focus === t.thread_id || o.picked.includes(t.thread_id)}
                    onPress={() => pickOrFocus(t.thread_id)}
                  />
                ))}
              </View>
            </Card>
          ) : null}
        </>
      )}

      <Ghosts data={data} onGhost={setGhost} onAll={() => goTab('possibilities')} />
      <AllRuns runs={runs} />

      {/* Below 1000pt the story and comparison open in this bottom sheet too (design §8). */}
      <Sheet visible={!!sheet} onClose={closeSheet} title={sheetTitle}>
        {sheet === 'moment' && beads ? <MomentCard t={beads.t} m={beads.m} n={keyNo[beads.t.thread_id]} color={colorOf(beads.t)} res={res} /> : null}
        {sheet === 'nexus' && nexus ? <NexusCard nexus={nexus} byId={byId} keyNo={keyNo} colorOf={colorOf} res={res} onCompare={compareTwo} /> : null}
        {sheet === 'ghost' && ghost ? <GhostCard ghost={ghost} /> : null}
        {sheet === 'story' ? storyPanel : null}
      </Sheet>
    </View>
  );
}

function Toggle({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  return (
    <Row gap={6}>
      <Switch value={value} onValueChange={onChange} accessibilityLabel={label} />
      <T v="small">{label}</T>
    </Row>
  );
}

/** "Known now" — real evidence, on the far side of the present line from every simulated value. */
function KnownNow({ data, onOpen }: { data: Futures; onOpen: () => void }) {
  const { c } = useTheme();
  const ev = data.evidence;
  const gaps = ev.gaps.filter((g) => g.kind !== 'FORECAST_INPUT').slice(0, 2);
  const counts: [string, number, string][] = [
    ['✓', ev.verified, 'verified'],
    ['✎', ev.user, 'you said'],
    ['⌛', ev.stale, 'stale'],
    ['?', ev.unknown, 'unknown'],
  ];
  if (ev.contested) counts.push(['⚠', ev.contested, 'contested']);
  return (
    <Card tone="ground">
      <Row between>
        <T v="eyebrow" color={c.ground}>
          Known now · evidence as of {fmtTime(data.evidence_as_of, 'minute', true)}
        </T>
        <Button small kind="ghost" label="Open evidence →" onPress={onOpen} />
      </Row>
      <Row gap={14} style={{ marginTop: 4 }}>
        {counts.map(([g, n, l]) => (
          <T key={l} v="small" color={c.text}>
            <T v="small" color={c.ground}>
              {g}{' '}
            </T>
            <T v="small" color={c.text} style={{ fontWeight: '700' }}>
              {n}
            </T>{' '}
            {l}
          </T>
        ))}
      </Row>
      {gaps.map((g) => (
        <T key={g.question} v="small" style={{ marginTop: 6 }}>
          • {g.question.replace(/^Find: /, '')}{' '}
          <T v="small" color={c.muted}>
            — unknown
          </T>
        </T>
      ))}
      {ev.warnings.length ? (
        <T v="small" color={c.bad} style={{ marginTop: 6 }}>
          ⚠ A source contained instructions — treated as data only
        </T>
      ) : null}
    </Card>
  );
}

function PathRow({
  t,
  n,
  color,
  res,
  hidden,
  selected,
  onPress,
}: {
  t: Thread;
  n: number;
  color: string;
  res: string;
  hidden: boolean;
  selected: boolean;
  onPress: () => void;
}) {
  const { c } = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected }}
      accessibilityLabel={`Path ${n}: ${t.label}. ${DETAIL_WORD[t.detail]}. Outcome: ${t.outcome_label}${hidden ? '. Hidden on the map' : ''}`}
      onPress={onPress}
      style={({ pressed }) => ({
        flexDirection: 'row',
        gap: 10,
        alignItems: 'flex-start',
        padding: 10,
        borderRadius: 12,
        borderWidth: 1,
        borderColor: selected ? color : c.line,
        backgroundColor: pressed || selected ? c.surface2 : 'transparent',
        opacity: hidden ? 0.6 : 1,
      })}
    >
      <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: color, alignItems: 'center', justifyContent: 'center', marginTop: 1 }}>
        <T v="tiny" color={c.onOutcome} style={{ fontWeight: '700' }} allowFontScaling={false}>
          {n}
        </T>
      </View>
      <View style={{ flex: 1, minWidth: 0 }}>
        <T v="body" style={{ fontWeight: '600' }}>
          {t.label}
        </T>
        <T v="tiny">
          {DETAIL_WORD[t.detail]}
          {t.also.length ? ` · also ${t.also.join(', ')}` : ''}
          {t.carries.length ? ` · ${t.carries.join(' · ')}` : ''}
        </T>
      </View>
      <View style={{ alignItems: 'flex-end', maxWidth: '38%' }}>
        <T v="small" color={color} style={{ textAlign: 'right', fontWeight: '600' }}>
          {t.outcome_label}
        </T>
        <T v="tiny">{fmtTime(t.ended_at, res, true)}</T>
      </View>
    </Pressable>
  );
}

const KIND_WORD: Record<string, string> = {
  action: 'Agent action',
  agreement: 'Agreement',
  threshold: 'Threshold crossed',
  external: 'From outside',
  info: 'Information',
  blocked: 'Blocked by a rule',
  end: 'End',
  start: 'Start',
};

function MomentCard({ t, m, n, color, res }: { t: Thread; m: Moment; n: number; color: string; res: string }) {
  const { c } = useTheme();
  return (
    <View>
      <Row between>
        <T v="eyebrow">
          {KIND_WORD[m.kind] || m.kind} · {fmtTime(m.t, res, true)}
        </T>
        <SimStamp />
      </Row>
      <T v="h1" style={{ fontSize: 20, lineHeight: 26, marginTop: 8 }}>
        {m.headline}
      </T>
      <Row gap={6} style={{ marginTop: 6 }}>
        <Dot color={color} />
        <T v="small" color={c.muted}>
          on path {n} · {t.label}
        </T>
      </Row>
      {m.tier ? (
        <View style={{ marginTop: 6 }}>
          <TierMeter tier={m.tier} withWords />
        </View>
      ) : null}
      {m.rationale ? (
        <View style={{ marginTop: 8 }}>
          <T v="small" color={c.text}>
            “{m.reasoning || m.rationale}”
          </T>
          <T v="tiny">The Agent's stated reasoning inside the simulation — not something the real person said.</T>
          {m.reasoning && m.reasoning !== m.rationale ? <T v="mono">Exact trace: {m.rationale}</T> : null}
        </View>
      ) : null}
      {m.seq !== null ? <EventActions runId={t.rep_run_id} seq={m.seq} res={res} /> : null}
      <Button
        small
        kind="ghost"
        label="Open this path's full timeline →"
        onPress={() => router.push({ pathname: '/run/[id]', params: { id: t.rep_run_id, seq: m.seq !== null ? String(m.seq) : undefined } })}
        style={{ marginTop: 10 }}
      />
    </View>
  );
}

/** Where paths split: what each side did differently, with a shortcut to compare the two. */
function NexusCard({
  nexus,
  byId,
  keyNo,
  colorOf,
  res,
  onCompare,
}: {
  nexus: Nexus;
  byId: Record<string, Thread>;
  keyNo: Record<string, number>;
  colorOf: (t: { family: string }) => string;
  res: string;
  onCompare: (a: string, b: string) => void;
}) {
  const { c } = useTheme();
  return (
    <View style={{ gap: 12 }}>
      <T v="eyebrow">Paths split · {fmtTime(nexus.kids[0].split_at, res, true)}</T>
      {nexus.kids.map((t) => {
        const parent = byId[t.parent || ''];
        if (!parent) return null;
        return (
          <Card key={t.thread_id}>
            <Row gap={6}>
              <Dot color={colorOf(parent)} />
              <T v="small" color={c.text} style={{ fontWeight: '600' }}>
                {keyNo[parent.thread_id]}. {parent.label}
              </T>
            </Row>
            <T v="small">{t.first_difference?.parent || '—'}</T>
            <Row gap={6} style={{ marginTop: 10 }}>
              <Dot color={colorOf(t)} />
              <T v="small" color={c.text} style={{ fontWeight: '600' }}>
                {keyNo[t.thread_id]}. {t.label}
              </T>
            </Row>
            <T v="small">{t.first_difference?.self || '—'}</T>
            {t.carries.length ? <T v="tiny">{t.carries.join(' · ')}</T> : null}
            <Row between style={{ marginTop: 8 }}>
              <T v="tiny">{t.shared_events || 0} shared moments of history before this split</T>
              <Button small label="Compare these two" onPress={() => onCompare(parent.thread_id, t.thread_id)} />
            </Row>
          </Card>
        );
      })}
    </View>
  );
}

function GhostCard({ ghost }: { ghost: Ghost }) {
  return (
    <View style={{ gap: 10 }}>
      <Pill tone={ghost.kind === 'ruled_out' ? 'bad' : 'default'}>
        {
          {
            ruled_out: 'Ruled out by an evidenced rule',
            set_aside: 'Set aside by the budget — not infeasible',
            reopened: 'Reopened after new evidence',
            unsimulated: 'Identified, not simulated',
          }[ghost.kind]
        }
      </Pill>
      <T v="lede">{humanize(ghost.why)}.</T>
      {ghost.citation.length ? <T v="small">Evidence cited: {ghost.citation.join(', ')}</T> : null}
      <T v="tiny">Nothing on the map should be read as exhaustive. Possibilities can exist that nobody identified.</T>
    </View>
  );
}

function Ghosts({ data, onGhost, onAll }: { data: Futures; onGhost: (g: Ghost) => void; onAll: () => void }) {
  const { c } = useTheme();
  if (!data.ghosts.length) return null;
  const glyph = { ruled_out: '✂', set_aside: '⏸', reopened: '↺', unsimulated: '┄' } as const;
  return (
    <Card>
      <Row between>
        <T v="h3">Identified, not simulated</T>
        <Button small kind="ghost" label={`All ${data.ghosts_total} possibilities →`} onPress={onAll} />
      </Row>
      <T v="tiny">Coverage is not exhaustive. These were identified but not simulated — and nobody can list what was never identified.</T>
      <Row style={{ marginTop: 8 }}>
        {data.ghosts.map((g) => (
          <Pressable key={g.id} accessibilityRole="button" onPress={() => onGhost(g)}>
            <Pill color={g.kind === 'ruled_out' ? c.cut : undefined}>{`${glyph[g.kind] || '┄'} ${g.label}`}</Pill>
          </Pressable>
        ))}
      </Row>
    </Card>
  );
}

const MODE_GROUPS: [string, string][] = [
  ['depth', 'Detailed simulations'],
  ['screening', 'Exploratory previews'],
  ['sensitivity', 'Sensitivity probes'],
];

/** Every run (GET /v1/decisions/{id}/runs) as trajectory cards; each opens its timeline explorer. */
function AllRuns({ runs }: { runs: RunCard[] | null }) {
  const { c } = useTheme();
  const [open, setOpen] = useState<Record<string, boolean>>({ depth: true });
  if (!runs || !runs.length) return null;
  return (
    <View>
      <T v="h2">Every run</T>
      <Banner tone="sim">Simulated trajectories under explicit assumptions. The number of runs is coverage inside one model — never a likelihood.</Banner>
      {MODE_GROUPS.map(([mode, title]) => {
        const rs = runs.filter((r) => (r.fidelity_mode || 'screening') === mode);
        if (!rs.length) return null;
        return (
          <View key={mode} style={{ marginBottom: 8 }}>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ expanded: !!open[mode] }}
              onPress={() => setOpen((s) => ({ ...s, [mode]: !s[mode] }))}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 }}
            >
              <T v="h3">
                {open[mode] ? '▾' : '▸'} {title}
              </T>
              <Pill>{String(rs.length)}</Pill>
            </Pressable>
            {open[mode]
              ? rs.map((r) => (
                  <Pressable
                    key={r.run_id}
                    accessibilityRole="link"
                    accessibilityLabel={`${r.name}. ${statusWords(r.status)}. Open timeline.`}
                    onPress={() => router.push({ pathname: '/run/[id]', params: { id: r.run_id } })}
                    style={({ pressed }) => ({
                      backgroundColor: pressed ? c.surface2 : c.surface,
                      borderWidth: 1,
                      borderColor: c.line,
                      borderRadius: 14,
                      padding: 12,
                      marginBottom: 8,
                    })}
                  >
                    <Row between wrap={false}>
                      <T v="body" style={{ fontWeight: '600', flex: 1 }}>
                        {r.name}
                      </T>
                      <SimStamp />
                    </Row>
                    <Row style={{ marginTop: 6 }}>
                      <Pill
                        tone={r.status === 'BUDGET_EXHAUSTED' ? 'warn' : r.status === 'FAILED' || r.status === 'MODEL_EXECUTION_BLOCKED' ? 'bad' : 'default'}
                      >
                        {statusWords(r.status)}
                      </Pill>
                      {r.weakest_tier ? <TierMeter tier={r.weakest_tier} withWords /> : null}
                    </Row>
                    {r.synopsis ? (
                      <T v="small" style={{ marginTop: 6 }}>
                        {r.synopsis}
                      </T>
                    ) : null}
                    {(r.caveats || []).map((cv) => (
                      <T key={cv} v="tiny">
                        • {cv}
                      </T>
                    ))}
                  </Pressable>
                ))
              : null}
          </View>
        );
      })}
    </View>
  );
}
