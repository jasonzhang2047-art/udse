/* Evidence — "What we know now" (docs/design/ui-redesign.md §5.6). Real evidence only: simulated events can
 * never appear here. Warm "ground" styling keeps it visually distinct from simulated futures. */

import { useEffect, useState } from 'react';
import { Pressable, View } from 'react-native';

import { Banner, Card, ErrorBox, Loading, OfflineNote, Pill, Row, T } from '@/components/ui';
import { fmtTime, fmtVal, humanize, shortHost } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { Claim, EvidenceBundle, WorldModel } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const STATUS_GLYPH: Record<string, [string, string]> = {
  verified: ['✓', 'Verified'],
  user: ['✎', 'You said'],
  stale: ['⌛', 'Stale'],
  unknown: ['?', 'Unknown'],
  contested: ['⚠', 'Contested / not certifiable'],
  hypothesis: ['◇', 'Hypothesis'],
  quarantined: ['⛔', 'Quarantined'],
  other: ['·', 'Unverified extract'],
};

function claimClass(c: Claim): string {
  if (c.object === 'UNKNOWN') return 'unknown';
  if (c.status === 'STALE') return 'stale';
  if (c.status === 'QUARANTINED') return 'quarantined';
  if (c.status === 'CONFLICTED' || c.record_type === 'contextual_lead') return 'contested';
  if (c.status === 'HYPOTHESIS') return 'hypothesis';
  if (c.status === 'VERIFIED') return 'verified';
  if (c.origin_kind === 'USER_ASSERTED') return 'user';
  return 'other';
}

export function EvidenceTab({ id, refresh }: { id: string; refresh: number }) {
  const { c } = useTheme();
  const b = useApi<EvidenceBundle>(`/v1/decisions/${id}/evidence`);
  const w = useApi<WorldModel>(`/v1/decisions/${id}/world-model`);
  const [showNeeds, setShowNeeds] = useState(false);
  useEffect(() => {
    if (refresh) {
      b.reload();
      w.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  if (b.error) return <ErrorBox error={b.error} onRetry={b.reload} />;
  if (!b.data) return <Loading label="Loading evidence" />;
  const bundle = b.data;
  const names: Record<string, string> = {};
  (w.data?.inputs || []).forEach((i) => {
    if (i.id.startsWith('entity:')) names[i.id.slice(7)] = i.label;
  });
  const asOf = Date.parse(bundle.as_of);
  const groups: Record<string, Claim[]> = {};
  bundle.claims.forEach((cl) => (groups[cl.subject] = groups[cl.subject] || []).push(cl));
  const gaps = bundle.needs.filter((n) => ['UNRESOLVED', 'ASK_USER', 'SKIPPED_PERMISSION'].includes(n.status));
  const order = ['UNRESOLVED', 'ASK_USER', 'SKIPPED_PERMISSION', 'OPEN', 'RESOLVED'];
  const needs = [...bundle.needs].sort((x, y) => order.indexOf(x.status) - order.indexOf(y.status) || x.priority - y.priority);

  return (
    <View>
      <OfflineNote cachedAt={b.cachedAt} />
      <T v="lede">
        What we actually know as of{' '}
        <T v="lede" color={c.text} style={{ fontWeight: '700' }}>
          {fmtTime(bundle.as_of, 'minute', true)}
        </T>{' '}
        — the ground every future stands on. Simulated events can never appear here.
      </T>
      {bundle.warnings.map((x) => (
        <Banner key={x} tone="bad" style={{ marginTop: 10 }}>
          {`⚠ ${x}`}
        </Banner>
      ))}

      {gaps.length ? (
        <>
          <T v="h2">Gaps that matter</T>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
            {gaps.map((n) => (
              <Card key={n.need_id} style={{ flexGrow: 1, flexBasis: 280 }}>
                <T v="eyebrow">
                  {humanize(n.kind.toLowerCase())} · {n.status === 'ASK_USER' ? 'please tell us' : 'still unknown'}
                </T>
                <T v="h3" style={{ marginTop: 6 }}>
                  {n.question.replace(/^Find: /, '')}
                </T>
                <T v="small" color={c.muted} style={{ marginTop: 4 }}>
                  {n.consequence_if_unresolved}
                </T>
              </Card>
            ))}
          </View>
        </>
      ) : null}

      <T v="h2">What the world looks like now</T>
      <Row gap={12} style={{ marginBottom: 10 }}>
        {Object.values(STATUS_GLYPH)
          .slice(0, 5)
          .map(([g, l]) => (
            <T key={l} v="tiny">
              {g} {l}
            </T>
          ))}
        <T v="tiny">bar = freshness remaining</T>
      </Row>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
        {Object.entries(groups).map(([subj, cs]) => (
          <Card key={subj} tone="ground" style={{ flexGrow: 1, flexBasis: 300 }}>
            <Row between>
              <T v="h3">{names[subj] || humanize(subj)}</T>
              <T v="tiny">
                {cs.length} fact{cs.length > 1 ? 's' : ''}
              </T>
            </Row>
            {cs.map((cl) => {
              const k = claimClass(cl);
              const [glyph, word] = STATUS_GLYPH[k];
              return (
                <View key={cl.claim_id} style={{ flexDirection: 'row', gap: 10, paddingVertical: 8, borderTopWidth: 1, borderTopColor: c.line }}>
                  <T
                    v="body"
                    color={k === 'contested' || k === 'quarantined' ? c.bad : k === 'stale' || k === 'unknown' ? c.warn : c.ground}
                    accessibilityLabel={word}
                  >
                    {glyph}
                  </T>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <T v="small" color={c.text}>
                      {humanize(cl.predicate)}
                    </T>
                    <T v="tiny">
                      {word} · {shortHost(cl.source_refs[0]?.uri)}
                      {cl.notes.length ? ` · ${cl.notes[0]}` : ''}
                    </T>
                    <Freshness claim={cl} asOf={asOf} />
                  </View>
                  <T v="small" color={c.text} style={{ fontWeight: '600', maxWidth: '40%', textAlign: 'right' }}>
                    {fmtVal(typeof cl.object === 'number' ? { value: cl.object, unit: cl.unit } : cl.object)}
                  </T>
                </View>
              );
            })}
          </Card>
        ))}
      </View>

      {bundle.conflicts.length ? (
        <>
          <T v="h2">Conflicts</T>
          {bundle.conflicts.map((x, i) => (
            <Card key={i}>
              <T v="small">
                {humanize(x.subject)} · {humanize(x.predicate)}: {x.resolution}
              </T>
            </Card>
          ))}
        </>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: showNeeds }}
        onPress={() => setShowNeeds(!showNeeds)}
        style={{ paddingVertical: 10 }}
      >
        <T v="h3">
          {showNeeds ? '▾' : '▸'} All research needs ({needs.length})
        </T>
      </Pressable>
      {showNeeds
        ? needs.map((n) => (
            <Card key={n.need_id}>
              <Row between>
                <T v="small" color={c.text} style={{ flex: 1 }}>
                  {n.question}
                </T>
                <Pill tone={n.status === 'RESOLVED' ? 'ok' : n.status === 'OPEN' ? 'default' : 'warn'}>{humanize(n.status.toLowerCase())}</Pill>
              </Row>
              <T v="tiny" style={{ marginTop: 4 }}>
                {humanize(n.kind.toLowerCase())} · priority {n.priority} · {n.priority_rationale}
              </T>
              {n.status !== 'RESOLVED' ? <T v="tiny">{n.consequence_if_unresolved}</T> : null}
            </Card>
          ))
        : null}

      {bundle.rejected.length ? (
        <>
          <T v="h2">Refused at the gate</T>
          <Card>
            {bundle.rejected.map((r, i) => (
              <T key={i} v="small">
                ⛔ {r.claim_id || ''} — {r.reason}
              </T>
            ))}
          </Card>
        </>
      ) : null}
    </View>
  );
}

function Freshness({ claim, asOf }: { claim: Claim; asOf: number }) {
  const { c } = useTheme();
  if (!claim.observed_at || !claim.expires_at) return null;
  const o = Date.parse(claim.observed_at);
  const e = Date.parse(claim.expires_at);
  const expired = asOf > e;
  const f = Math.min(1, Math.max(0, (asOf - o) / Math.max(1, e - o)));
  const left = expired ? 1 : 1 - f;
  return (
    <View
      accessible
      accessibilityLabel={`Observed ${claim.observed_at.slice(0, 10)}, valid until ${claim.expires_at.slice(0, 10)}${expired ? ', expired' : ''}`}
      style={{ height: 4, borderRadius: 2, backgroundColor: c.line, marginTop: 5, overflow: 'hidden' }}
    >
      <View style={{ width: `${left * 100}%`, height: 4, backgroundColor: expired ? c.bad : c.ground, opacity: expired ? 0.5 : 1 }} />
    </View>
  );
}
