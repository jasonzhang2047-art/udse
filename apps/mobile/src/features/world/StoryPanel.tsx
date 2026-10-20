/* Thread story (docs/design/ui-redesign.md §5.4): synopsis, moments rail grouped into chapters with an
 * on-demand "Why?" chain, state at the cursor with sparklines, what the path rests on, and your reaction. */

import { router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { WhyChain } from '@/components/Causes';
import { Sparkline } from '@/components/Charts';
import { Reaction } from '@/components/Reaction';
import { Button, Card, ErrorBox, Loading, Pill, Row, SimStamp, T, TierMeter } from '@/components/ui';
import { api } from '@/lib/api';
import { DETAIL_WORD, fmtNum, fmtTime, ms, simTimeAt, tierWords } from '@/lib/format';
import { valueAt } from '@/lib/futures';
import { useTheme } from '@/lib/theme';
import type { Futures, Moment, Thread, Why } from '@/lib/types';

function chapterKey(iso: string, res: string): string {
  if (res === 'minute' || res === 'hour') return `${iso.slice(11, 13)}:00`;
  if (res === 'day' || res === 'week') return fmtTime(iso, 'day');
  return `Q${Math.ceil(Number(iso.slice(5, 7)) / 3)} ${iso.slice(0, 4)}`;
}

export function StoryPanel({
  data,
  t,
  keyNo,
  colorOf,
  cursor,
  decisionId,
  onClose,
  bare,
}: {
  data: Futures;
  t: Thread;
  keyNo: Record<string, number>;
  colorOf: (t: { family: string }) => string;
  cursor: number | null;
  decisionId: string;
  onClose: () => void;
  /** Rendered inside a sheet that has its own title and close button. */
  bare?: boolean;
}) {
  const { c, person } = useTheme();
  const res = data.horizon.resolution;
  const col = colorOf(t);
  const people = Object.fromEntries(data.people.map((p, i) => [p.id, i]));
  const parent = t.parent ? data.threads.find((x) => x.thread_id === t.parent) : null;
  const chapters: { key: string; items: Moment[] }[] = [];
  for (const m of t.moments) {
    if (m.kind === 'start') continue;
    const k = chapterKey(m.t, res);
    if (!chapters.length || chapters[chapters.length - 1].key !== k) chapters.push({ key: k, items: [] });
    chapters[chapters.length - 1].items.push(m);
  }
  const finalTier = t.tier_path.length ? t.tier_path[t.tier_path.length - 1][1] : null;
  const at = cursor !== null && cursor < ms(t.ended_at) ? cursor : null;
  const mark = (m: Moment) =>
    m.kind === 'action'
      ? person(people[m.actor || ''] ?? 0)
      : m.kind === 'threshold'
        ? c.warn
        : m.kind === 'external'
          ? c.present
          : m.kind === 'blocked'
            ? c.cut
            : col;

  return (
    <Card style={[{ borderColor: col + '88' }, bare && { borderWidth: 0, padding: 0, backgroundColor: 'transparent' }]}>
      {bare ? null : (
        <Row between>
          <T v="eyebrow">Path {keyNo[t.thread_id]}</T>
          <Button small kind="ghost" label="Close" onPress={onClose} />
        </Row>
      )}
      <T v="h1" accessibilityRole="header" style={{ marginTop: 4 }}>
        {t.label}
      </T>
      <Row style={{ marginTop: 8 }}>
        <Pill color={col}>{`${t.horizon_reached ? '○' : t.stopped_early ? '⋯' : '■'} ${t.outcome_label}`}</Pill>
        <Pill>{DETAIL_WORD[t.detail]}</Pill>
        <SimStamp />
      </Row>
      <T v="lede" style={{ marginTop: 10 }}>
        {t.synopsis}
      </T>
      {parent ? (
        <T v="small" color={c.muted} style={{ marginTop: 6 }}>
          {t.shared_events
            ? `Shares ${t.shared_events} moments of history with path ${keyNo[parent.thread_id]}, then splits at ${fmtTime(t.split_at, res, true)}`
            : `Differs from path ${keyNo[parent.thread_id]} from the start`}
          : {t.first_difference?.self || ''}
        </T>
      ) : null}
      {t.carries.length ? (
        <Row style={{ marginTop: 8 }}>
          {t.carries.map((x) => (
            <Pill key={x} tone="sim">
              {x}
            </Pill>
          ))}
        </Row>
      ) : null}

      {data.metrics.length ? (
        <>
          <T v="h3" style={{ marginTop: 14 }}>
            {at !== null ? `At ${simTimeAt(at, data.horizon.start, res)}` : 'Where it ends up'}
          </T>
          {data.metrics.map((m) => {
            const s = t.series[m.id];
            const v = valueAt(s, at ?? ms(t.ended_at));
            return (
              <Row key={m.id} between wrap={false} style={{ paddingVertical: 4 }}>
                <View style={{ flex: 1 }}>
                  <T v="small">{m.label}</T>
                  <Sparkline series={s} end={t.ended_at} color={col} />
                </View>
                <T v="body" style={{ fontWeight: '600' }}>
                  {fmtNum(v, m.unit)}
                </T>
              </Row>
            );
          })}
          <T v="tiny">Simulated values · committed points only, never interpolated.</T>
        </>
      ) : null}

      <T v="h3" style={{ marginTop: 14 }}>
        Moments
      </T>
      {chapters.map((ch) => (
        <View key={ch.key}>
          <T v="eyebrow" style={{ marginTop: 10 }}>
            {ch.key}
          </T>
          <View style={{ borderLeftWidth: 2, borderLeftColor: col + '66', marginLeft: 5, paddingLeft: 12, marginTop: 4 }}>
            {ch.items.map((m) => (
              <MomentRow key={m.i} m={m} t={t} res={res} color={mark(m)} shared={!!t.parent && m.i < t.split_index} />
            ))}
          </View>
        </View>
      ))}

      <T v="h3" style={{ marginTop: 14 }}>
        This path rests on
      </T>
      {finalTier ? (
        <Row style={{ marginTop: 4 }}>
          <TierMeter tier={finalTier} />
          <T v="small" style={{ flex: 1 }}>
            {tierWords(finalTier)} (weakest support reached along the path).
          </T>
        </Row>
      ) : (
        <T v="small">—</T>
      )}
      <T v="tiny">Evidence support describes how well mechanisms are grounded — never how likely this future is.</T>

      <T v="h3" style={{ marginTop: 14 }}>
        Your reaction
      </T>
      <Reaction decisionId={decisionId} runIds={t.run_ids} pathLabel={t.label} />

      <Button label="Open timeline explorer →" onPress={() => router.push({ pathname: '/run/[id]', params: { id: t.rep_run_id } })} style={{ marginTop: 14 }} />
      <T v="tiny" style={{ marginTop: 6 }}>
        Runs: {t.run_ids.map((r) => r.slice(-6)).join(', ')}
      </T>
    </Card>
  );
}

function MomentRow({ m, t, res, color, shared }: { m: Moment; t: Thread; res: string; color: string; shared: boolean }) {
  const { c } = useTheme();
  const [why, setWhy] = useState<Why | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const toggle = async () => {
    if (why) return setWhy(null);
    if (m.seq === null) return;
    setBusy(true);
    setErr(null);
    try {
      setWhy(await api.get<Why>(`/v1/runs/${t.rep_run_id}/why/${m.seq}`));
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };
  const quote = m.kind !== 'end' && m.rationale ? m.reasoning || m.rationale : null;
  return (
    <View style={{ paddingVertical: 6, opacity: shared ? 0.6 : 1 }}>
      <View
        style={{ position: 'absolute', left: -18, top: 11, width: 10, height: 10, borderRadius: 5, backgroundColor: c.bg, borderWidth: 2, borderColor: color }}
      />
      <T v="mono">
        {fmtTime(m.t, res, true)}
        {shared ? ' · shared history' : ''}
      </T>
      <T v="small" color={c.text}>
        {m.headline}
      </T>
      {quote ? (
        <T v="tiny">
          “{quote.slice(0, 160)}
          {quote.length > 160 ? '…' : ''}”
        </T>
      ) : m.kind === 'end' && m.rationale ? (
        <T v="tiny">{m.rationale}</T>
      ) : null}
      {m.seq !== null ? (
        <Button small kind="ghost" label={why ? 'Hide why' : 'Why?'} onPress={toggle} style={{ marginTop: 4 }} />
      ) : null}
      {busy ? <Loading label="Tracing causes" /> : null}
      {err ? <ErrorBox error={err} /> : null}
      {why ? <WhyChain why={why} res={res} /> : null}
    </View>
  );
}
