/* Your Agent (docs/design/ui-redesign.md §5.9): how your Agent represents you. Private to you. Changes only
 * through suggested updates you approve (POST /v1/profiles/{id}/approvals) and can be rolled back. */

import { useState } from 'react';
import { View } from 'react-native';

import { Banner, Button, Card, Empty, ErrorBox, Loading, OfflineNote, Pill, Row, Screen, T } from '@/components/ui';
import { api } from '@/lib/api';
import { fmtVal, humanize } from '@/lib/format';
import { useSession } from '@/lib/session';
import { useTheme } from '@/lib/theme';
import type { Profile, ProfileFeature, Value } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const STATUS: Record<string, string> = { CONFIRMED: 'Confirmed by you', INFERRED: 'Inferred', UNKNOWN: "We don't know yet", HARD_CONSTRAINT: 'Hard boundary' };
const prefLabel = (key: string) => humanize(key.split('.').slice(1).join(' ')).replace(/\bpp\b/, 'per person');
const show = (v: unknown) => (v === undefined || v === null ? '—' : fmtVal(v as Value));
const weightOf = (v: Value) =>
  v && typeof v === 'object' && !Array.isArray(v) && typeof (v as { weight?: unknown }).weight === 'number' ? (v as { weight: number }).weight : 0;
const unitOf = (v: Value) => (v && typeof v === 'object' && !Array.isArray(v) ? ((v as { unit?: string }).unit ?? null) : null);

export default function Agent() {
  const { me } = useSession();
  const pid = me?.profile_ids[0];
  const p = useApi<Profile>(pid ? `/v1/profiles/${pid}` : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ tone: 'ok' | 'bad'; text: string } | null>(null);

  const act = async (key: string, path: 'approvals' | 'rollback', body: Record<string, unknown>, done: string) => {
    if (!pid) return;
    setBusy(key);
    setMsg(null);
    try {
      await api.post(`/v1/profiles/${pid}/${path}`, body);
      setMsg({ tone: 'ok', text: done });
      p.reload();
    } catch (e) {
      setMsg({ tone: 'bad', text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Screen onRefresh={p.reload} refreshing={p.loading && !!p.data}>
      <T v="eyebrow">Your Agent</T>
      <T v="display" accessibilityRole="header" style={{ marginTop: 6 }}>
        How your Agent represents you
      </T>
      <T v="lede" style={{ marginTop: 8, marginBottom: 12 }}>
        Private to you. Simulated behaviour never changes this — only things you explicitly say, after you approve them.
      </T>
      {!pid ? <Empty>No Agent profile for this participant.</Empty> : null}
      <OfflineNote cachedAt={p.cachedAt} />
      {p.error ? <ErrorBox error={p.error} onRetry={p.reload} /> : null}
      {pid && !p.data && !p.error ? <Loading label="Loading your Agent" /> : null}
      {msg ? <Banner tone={msg.tone}>{msg.text}</Banner> : null}
      {p.data ? <ProfileView p={p.data} busy={busy} offline={!!p.cachedAt} act={act} /> : null}
    </Screen>
  );
}

function ProfileView({
  p,
  busy,
  offline,
  act,
}: {
  p: Profile;
  busy: string | null;
  offline: boolean;
  act: (key: string, path: 'approvals' | 'rollback', body: Record<string, unknown>, done: string) => void;
}) {
  const { c } = useTheme();
  const cur = p.current;
  const prefs = cur.features.filter((f) => f.key.startsWith('preference.'));
  const bounds = cur.features.filter((f) => f.status === 'HARD_CONSTRAINT' || f.key.startsWith('constraint.'));
  const beh = cur.features.filter((f) => f.key.startsWith('behavior.'));
  const maxW = Math.max(1e-9, ...prefs.map((f) => Math.abs(weightOf(f.value))));
  const pending = p.proposals.filter((x) => x.status === 'PROPOSED');
  const decided = p.proposals.filter((x) => x.status !== 'PROPOSED');
  const evidence = Object.fromEntries(p.evidence.map((e) => [e.event_id, e]));

  return (
    <View>
      <T v="h2">Suggested updates {pending.length ? `(${pending.length})` : ''}</T>
      {!pending.length ? (
        <T v="small" color={c.muted}>
          Nothing waiting. Corrections you make on a path (“My Agent wouldn't do that…”) appear here for approval.
        </T>
      ) : null}
      {pending.map((x) => (
        <Card key={x.proposal_id} tone="warn">
          <Row between>
            <T v="h3" style={{ flex: 1 }}>
              {x.reason}
            </T>
            <Pill tone={x.mode === 'shadow' ? 'default' : 'warn'}>{x.mode === 'shadow' ? 'observed only' : 'needs your approval'}</Pill>
          </Row>
          {x.changes.map((ch, i) => (
            <T key={i} v="small" style={{ marginTop: 4 }}>
              {humanize(ch.key.split('.').pop())}: {show(ch.before?.value)} → {show(ch.after?.value)}
              {ch.after?.status === 'HARD_CONSTRAINT' ? ' (hard boundary)' : ''}
            </T>
          ))}
          <T v="tiny" style={{ marginTop: 4 }}>
            From: “{String(evidence[x.evidence_ids[0]]?.payload?.statement ?? 'your feedback')}”
          </T>
          {x.mode === 'shadow' ? <T v="tiny">Shadow mode: recorded for calibration, never applied automatically.</T> : null}
          <Row style={{ marginTop: 10 }}>
            <Button
              small
              kind="primary"
              label="Approve"
              disabled={!!busy || offline}
              onPress={() => act(x.proposal_id, 'approvals', { proposal_id: x.proposal_id, approve: true }, 'Approved — your profile has a new version.')}
            />
            <Button
              small
              kind="ghost"
              label="Not right"
              disabled={!!busy || offline}
              onPress={() => act(x.proposal_id, 'approvals', { proposal_id: x.proposal_id, approve: false }, 'Rejected — nothing changed.')}
            />
          </Row>
        </Card>
      ))}

      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 8 }}>
        <Card style={{ flexGrow: 1, flexBasis: 300 }}>
          <T v="eyebrow">What you care about</T>
          {prefs.length ? (
            prefs.map((f) => <Pref key={f.key} f={f} maxW={maxW} />)
          ) : (
            <T v="small" color={c.muted}>
              Nothing yet.
            </T>
          )}
        </Card>
        <View style={{ flexGrow: 1, flexBasis: 300 }}>
          {bounds.map((f) => (
            <Card key={f.key} tone="ground">
              <T v="eyebrow">⛨ Hard boundary</T>
              <T v="h3" style={{ marginTop: 6, fontWeight: '400' }}>
                {f.note && !f.note.startsWith('user-authorized')
                  ? f.note.replace(/^from EXPLICIT_CORRECTION: /, '')
                  : `${humanize(f.key.split('.').slice(1).join(' '))} ${show((f.value as { value?: unknown })?.value ?? f.value)}`}
              </T>
              <T v="tiny">{STATUS[f.status] || f.status}</T>
            </Card>
          ))}
          <Card>
            <T v="eyebrow">How you tend to decide</T>
            {beh.length ? (
              beh.map((f) => (
                <Row key={f.key} between style={{ paddingVertical: 6 }}>
                  <View style={{ flex: 1 }}>
                    <T v="small" color={c.text} style={{ fontWeight: '600' }}>
                      {humanize(f.key.split('.').pop())}
                    </T>
                    <T v="tiny">{STATUS[f.status] || f.status}</T>
                  </View>
                  {f.status === 'UNKNOWN' ? (
                    <Pill tone="warn">unknown</Pill>
                  ) : typeof f.value === 'number' ? (
                    <Row gap={6}>
                      <View style={{ width: 70, height: 6, borderRadius: 3, backgroundColor: c.line2 }}>
                        <View style={{ width: `${Math.round(f.value * 100)}%`, height: 6, borderRadius: 3, backgroundColor: c.ground }} />
                      </View>
                      <T v="small">{Math.round(f.value * 100)}%</T>
                    </Row>
                  ) : (
                    <T v="small">{show(f.value)}</T>
                  )}
                </Row>
              ))
            ) : (
              <T v="small" color={c.muted}>
                Nothing yet.
              </T>
            )}
          </Card>
        </View>
      </View>

      <T v="h2">History</T>
      <Card>
        {p.versions
          .slice()
          .reverse()
          .map((v) => (
            <View key={v.version} style={{ paddingVertical: 8, borderTopWidth: v.version === cur.version ? 0 : 1, borderTopColor: c.line }}>
              <T v="mono">
                v{v.version} · {v.created_at.slice(0, 10)}
              </T>
              <T v="small" color={c.text}>
                {v.reason}
              </T>
              {v.version !== cur.version ? (
                <Button
                  small
                  kind="ghost"
                  label="Restore this version"
                  disabled={!!busy || offline}
                  onPress={() => act(`rb${v.version}`, 'rollback', { to_version: v.version }, `Restored version ${v.version} as a new version.`)}
                  style={{ marginTop: 6 }}
                />
              ) : (
                <T v="tiny">current</T>
              )}
            </View>
          ))}
      </Card>
      {decided.length ? (
        <>
          <T v="h2">Past suggestions</T>
          {decided.map((x) => (
            <Card key={x.proposal_id}>
              <Row between>
                <T v="small" color={c.text} style={{ flex: 1 }}>
                  {x.reason}
                </T>
                <Pill tone={x.status === 'APPROVED' ? 'ok' : 'default'}>{humanize(x.status.toLowerCase())}</Pill>
              </Row>
            </Card>
          ))}
        </>
      ) : null}
    </View>
  );
}

function Pref({ f, maxW }: { f: ProfileFeature; maxW: number }) {
  const { c } = useTheme();
  const w = weightOf(f.value);
  const unit = unitOf(f.value);
  const k = Math.log1p(Math.abs(w) * 10) / Math.log1p(maxW * 10);
  const pct = Math.max(6, k * 50);
  const how = `${k > 0.66 ? 'Strongly ' : k < 0.33 ? 'Mildly ' : ''}${w >= 0 ? (unit ? 'prefers more' : 'wants this') : unit ? 'prefers less' : 'avoids this'}`;
  return (
    <View
      style={{ paddingVertical: 8, borderTopWidth: 1, borderTopColor: c.line }}
      accessible
      accessibilityLabel={`${prefLabel(f.key)}: ${how}. ${STATUS[f.status] || f.status}.`}
    >
      <T v="small" color={c.text} style={{ fontWeight: '600' }}>
        {prefLabel(f.key)}
      </T>
      <T v="tiny">
        {how.charAt(0).toUpperCase() + how.slice(1).toLowerCase()}
        {unit ? ` · per ${unit === 'USD' ? '$' : unit}` : ''} · {STATUS[f.status] || f.status} · {f.evidence_refs.length} evidence
      </T>
      <View style={{ height: 8, backgroundColor: c.surface2, borderRadius: 4, marginTop: 6 }}>
        <View style={{ position: 'absolute', left: '50%', top: -2, bottom: -2, width: 1, backgroundColor: c.line2 }} />
        <View
          style={[
            { position: 'absolute', top: 0, height: 8, borderRadius: 4, width: `${pct}%` },
            w >= 0 ? { left: '50%', backgroundColor: c.ok } : { right: '50%', backgroundColor: c.bad },
          ]}
        />
      </View>
      <Row between>
        <T v="tiny">avoid</T>
        <T v="tiny">want</T>
      </Row>
    </View>
  );
}
