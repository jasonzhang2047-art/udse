/* World model — "How this world works" (docs/design/ui-redesign.md §5.7) plus the compiled model's report:
 * per-metric fidelity tiers, scorecard, visible downgrades, structural unknowns and omissions, and the
 * reuse/transfer gate (GET /v1/decisions/{id}/world-model, /models and /models/{v}). */

import { useEffect, useMemo, useState } from 'react';
import { Pressable, useWindowDimensions, View } from 'react-native';

import { Banner, Card, ErrorBox, KV, Loading, OfflineNote, Pill, Row, Segmented, T, TierMeter } from '@/components/ui';
import { humanize, TIERS, tierShort, tierWords } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { ModelDetail, ModelSummary, Tier, WorldModel, WorldModelNode } from '@/lib/types';
import { useApi } from '@/lib/useApi';

export function ModelTab({
  id,
  models,
  version,
  refresh,
  onVersion,
}: {
  id: string;
  models: ModelSummary[];
  version: number | null;
  refresh: number;
  onVersion: (v: number) => void;
}) {
  const { c } = useTheme();
  const { width } = useWindowDimensions();
  const v = version ?? models[models.length - 1]?.version ?? null;
  const w = useApi<WorldModel>(v ? `/v1/decisions/${id}/world-model?version=${v}` : null);
  const m = useApi<ModelDetail>(v ? `/v1/decisions/${id}/models/${v}` : null);
  const [active, setActive] = useState<string | null>(null);
  const [showMech, setShowMech] = useState(false);
  useEffect(() => {
    if (refresh) {
      w.reload();
      m.reload();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refresh]);

  const reach = useMemo(() => {
    if (!active || !w.data) return null;
    const into: Record<string, string[]> = {};
    w.data.edges.forEach((e) => (into[e.to] = into[e.to] || []).push(e.from));
    const seen = new Set([active]);
    const stack = [active];
    while (stack.length) {
      const n = stack.pop()!;
      (into[n] || []).forEach((p) => {
        if (!seen.has(p)) {
          seen.add(p);
          stack.push(p);
        }
      });
    }
    return seen;
  }, [active, w.data]);

  if (!v) return <Banner tone="warn">No world model yet. The owner can build one with “Build world model”.</Banner>;
  if (w.error) return <ErrorBox error={w.error} onRetry={w.reload} />;
  if (!w.data) return <Loading label="Loading the world model" />;
  const wm = w.data;
  const vinfo = wm.versions.find((x) => x.version === wm.model.version);
  const wideFlow = width >= 900;
  const dim = (nid: string) => (reach && !reach.has(nid) ? 0.3 : 1);

  const node = (n: WorldModelNode, kind: 'input' | 'proc') => (
    <Pressable
      key={n.id}
      accessibilityRole="button"
      accessibilityState={{ selected: active === n.id }}
      onPress={() => setActive(active === n.id ? null : n.id)}
      style={{
        opacity: dim(n.id),
        borderWidth: 1,
        borderColor: active === n.id ? c.present : kind === 'input' ? c.ground + '66' : c.line2,
        backgroundColor: kind === 'input' ? c.groundBg : c.surface2,
        borderRadius: 12,
        padding: 10,
        marginBottom: 8,
        borderStyle: n.tier === 'C_ILLUSTRATIVE' || n.tier === 'X_LIMITED_OR_ABSTAIN' ? 'dashed' : 'solid',
      }}
    >
      <Row between wrap={false}>
        <T v="small" color={c.text} style={{ fontWeight: '600', flex: 1 }}>
          {n.label}
        </T>
        {n.tier ? <TierMeter tier={n.tier} /> : null}
      </Row>
      {n.counts ? (
        <T v="tiny">
          {Object.entries(n.counts)
            .filter(([, x]) => x)
            .map(([k, x]) => `${x} ${k}`)
            .join(' · ')}
        </T>
      ) : null}
      {n.kind === 'choice' ? (
        <T v="tiny">People's choice · {(n.actors || []).join(', ')}</T>
      ) : n.description ? (
        <T v="tiny">{n.description.length > 110 ? n.description.slice(0, 108) + '…' : n.description}</T>
      ) : null}
      {n.reuses ? (
        <T v="tiny" color={c.ok}>
          Reuses {n.reuses}
        </T>
      ) : null}
    </Pressable>
  );

  const missing = wm.missing.filter((x) => x.status === 'DISCOVERED_UNMODELED');
  const columns = [
    <View key="in" style={{ flex: 1, minWidth: 0 }}>
      <T v="eyebrow" style={{ marginBottom: 6 }}>
        What we start from
      </T>
      {wm.inputs.map((n) => node(n, 'input'))}
    </View>,
    <View key="proc" style={{ flex: 1, minWidth: 0 }}>
      <T v="eyebrow" style={{ marginBottom: 6 }}>
        {wideFlow ? 'How things change' : '↓ How things change'}
      </T>
      {wm.processes.map((n) => node(n, 'proc'))}
      {missing.map((x) => (
        <View
          key={x.id}
          style={{ opacity: dim(x.id), borderWidth: 1, borderStyle: 'dashed', borderColor: c.bad, borderRadius: 12, padding: 10, marginBottom: 8 }}
        >
          <T v="small" color={c.text} style={{ fontWeight: '600' }}>
            ? {x.label}
          </T>
          <T v="tiny" color={c.bad}>
            Missing — {x.description}
          </T>
        </View>
      ))}
    </View>,
    <View key="out" style={{ flex: 1, minWidth: 0 }}>
      <T v="eyebrow" style={{ marginBottom: 6 }}>
        {wideFlow ? 'What you look at' : '↓ What you look at'}
      </T>
      {wm.outcomes.map((o) => (
        <Pressable
          key={o.id}
          accessibilityRole="button"
          accessibilityState={{ selected: active === o.id }}
          accessibilityHint="Highlights everything this outcome depends on"
          onPress={() => setActive(active === o.id ? null : o.id)}
          style={{
            opacity: dim(o.id),
            borderWidth: 1,
            borderColor: active === o.id ? c.present : c.line2,
            borderRadius: 12,
            padding: 10,
            marginBottom: 8,
            backgroundColor: c.surface,
          }}
        >
          <Row between wrap={false}>
            <T v="small" color={c.text} style={{ fontWeight: '600', flex: 1 }}>
              {o.label}
            </T>
            <TierMeter tier={o.tier} />
          </Row>
          {o.computable ? (
            <T v="tiny">{tierWords(o.tier)}</T>
          ) : (
            <T v="tiny" color={c.bad}>
              Withheld — {o.reason}
            </T>
          )}
          {o.weakest.length && o.computable ? <T v="tiny">Weakest link: {o.weakest.join(', ')}</T> : null}
        </Pressable>
      ))}
    </View>,
  ];

  const detail = m.data;
  return (
    <View>
      <OfflineNote cachedAt={w.cachedAt} />
      <T v="lede">{wm.model.purpose}</T>
      {models.length > 1 ? (
        <View style={{ marginTop: 10 }}>
          <Segmented
            label="Model version"
            options={models.map((x) => ({ key: String(x.version), label: `v${x.version}${x.revision_reason ? ' · ' + x.revision_reason.slice(0, 28) : ''}` }))}
            value={String(wm.model.version)}
            onChange={(k) => onVersion(Number(k))}
          />
        </View>
      ) : null}
      {vinfo?.diff ? (
        <Banner tone="warn" style={{ marginTop: 10 }}>
          {`Expanded from v${vinfo.diff.from_version}: ${vinfo.diff.mechanisms_added.length ? 'added ' + vinfo.diff.mechanisms_added.map(humanize).join(', ') + '. ' : ''}${
            vinfo.diff.state_changed.length ? vinfo.diff.state_changed.length + ' facts changed. ' : ''
          }${vinfo.diff.banner}`}
        </Banner>
      ) : null}

      <T v="h2">How this world works</T>
      <T v="small" color={c.muted}>
        Tap an outcome to trace what it depends on. Meters show evidence support — never likelihood, never averaged.
      </T>
      <Card style={{ marginTop: 10 }}>
        <View style={{ flexDirection: wideFlow ? 'row' : 'column', gap: wideFlow ? 14 : 4 }}>{columns}</View>
        {active ? (
          <T v="tiny" style={{ marginTop: 4 }}>
            Highlighted: everything “{[...wm.inputs, ...wm.processes, ...wm.outcomes].find((x) => x.id === active)?.label}” depends on. Tap it again to clear.
          </T>
        ) : null}
      </Card>

      {m.error ? <ErrorBox error={m.error} onRetry={m.reload} /> : null}
      {!detail ? (
        m.loading ? (
          <Loading label="Loading the compiled model report" />
        ) : null
      ) : (
        <>
          <T v="h2">Evidence support by outcome</T>
          <Card>
            {Object.entries(detail.metric_fidelity).map(([k, t], i) => (
              <View key={k} style={{ paddingVertical: 8, borderTopWidth: i ? 1 : 0, borderTopColor: c.line }}>
                <Row between wrap={false}>
                  <T v="small" color={c.text} style={{ flex: 1 }}>
                    {wm.outcomes.find((o) => o.id === `metric:${k}`)?.label || humanize(k)}
                  </T>
                  <TierMeter tier={t} />
                  <T v="tiny" style={{ width: 120, textAlign: 'right' }}>
                    {tierShort(t)}
                  </T>
                </Row>
                {detail.non_computable_metrics[k] ? (
                  <T v="tiny" color={c.bad}>
                    {detail.non_computable_metrics[k]}
                  </T>
                ) : null}
              </View>
            ))}
            <T v="tiny" style={{ marginTop: 6 }}>
              Each outcome shows the weakest tier along its dependency path. Tiers describe evidence support, not forecast accuracy.
            </T>
          </Card>

          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: showMech }}
            onPress={() => setShowMech(!showMech)}
            style={{ paddingVertical: 10 }}
          >
            <T v="h3">
              {showMech ? '▾' : '▸'} Processes in detail ({detail.model.mechanisms.length})
            </T>
          </Pressable>
          {showMech
            ? detail.model.mechanisms.map((x) => (
                <Card key={x.id}>
                  <Row between>
                    <T v="small" color={c.text} style={{ fontWeight: '600', flex: 1 }}>
                      {humanize(x.id)}
                    </T>
                    <TierMeter tier={x.epistemic_tier} withWords />
                  </Row>
                  <T v="small" style={{ marginTop: 4 }}>
                    {x.description}
                  </T>
                  <Row style={{ marginTop: 6 }}>
                    <Pill>{humanize(x.operator)}</Pill>
                    {x.registry_ref ? <Pill tone="ok">{`reuses ${x.registry_ref.entry_id}@${x.registry_ref.version}`}</Pill> : null}
                    {(x.assumptions || []).map((a) => (
                      <Pill key={a} tone="warn">
                        {a}
                      </Pill>
                    ))}
                  </Row>
                </Card>
              ))
            : null}

          <T v="h2">What is not modelled</T>
          {detail.model.structural_unknowns.map((u) => (
            <Card key={u.id}>
              <Row>
                <Pill tone={u.impact === 'high' ? 'bad' : 'warn'}>{`${u.impact} impact`}</Pill>
                <Pill>{humanize(u.status.toLowerCase())}</Pill>
              </Row>
              <T v="small" color={c.text} style={{ marginTop: 6 }}>
                {u.description}
              </T>
              {u.affects_metrics.length ? <T v="tiny">Affects: {u.affects_metrics.map(humanize).join(', ')}</T> : null}
            </Card>
          ))}
          {wm.omissions.length ? (
            <>
              <T v="h3" style={{ marginVertical: 6 }}>
                Left out on purpose
              </T>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
                {wm.omissions.map((o) => (
                  <Card key={o.id} style={{ flexGrow: 1, flexBasis: 260 }}>
                    <T v="eyebrow">{o.risk} risk</T>
                    <T v="small" color={c.text} style={{ marginTop: 6 }}>
                      {o.description}
                    </T>
                    <T v="tiny">{o.reason}</T>
                  </Card>
                ))}
              </View>
            </>
          ) : null}

          <T v="h2">Checks</T>
          <Card>
            <Row style={{ marginBottom: 6 }}>
              <Pill tone={detail.passed ? 'ok' : 'bad'}>{detail.passed ? 'Passed compile-time checks' : 'Failed checks'}</Pill>
              <Pill>{humanize(detail.promotion_state.toLowerCase())}</Pill>
              {detail.allowed_run_modes.map((x) => (
                <Pill key={x}>{`${x} runs allowed`}</Pill>
              ))}
            </Row>
            <KV
              rows={Object.entries(detail.scorecard)
                .filter(([k]) => k !== 'interpretive_limitations')
                .map(([k, val]) => [humanize(k), String(val)])}
            />
            {(detail.scorecard.interpretive_limitations || []).map((x) => (
              <T key={x} v="tiny" style={{ marginTop: 4 }}>
                • {x}
              </T>
            ))}
            <T v="mono" style={{ marginTop: 8 }} numberOfLines={1}>
              {detail.hash}
            </T>
          </Card>
          <T v="h3" style={{ marginVertical: 6 }}>
            Visible downgrades ({detail.downgrades.length})
          </T>
          {detail.downgrades.length ? (
            <Card>
              {detail.downgrades.map((g) => (
                <T key={g.id} v="small" style={{ paddingVertical: 3 }}>
                  {humanize(g.id)}: {TIERS[g.from as Tier]?.short || g.from} → {TIERS[g.to as Tier]?.short || g.to} — {g.reason}
                </T>
              ))}
            </Card>
          ) : (
            <T v="small" color={c.muted}>
              No element was downgraded during compilation.
            </T>
          )}

          <T v="h2">Reuse and transfer checks</T>
          {detail.model.reuse_manifest.length ? (
            detail.model.reuse_manifest.map((r) => (
              <Card key={r.entry}>
                <Row between>
                  <T v="small" color={c.text} style={{ fontWeight: '600', flex: 1 }}>
                    {r.entry}
                  </T>
                  <Pill tone={r.used ? 'ok' : r.transfer_ok ? 'default' : 'bad'}>
                    {r.used ? 'used' : r.transfer_ok ? 'compatible, not used' : 'transfer rejected'}
                  </Pill>
                </Row>
                <T v="tiny">
                  {humanize(r.kind)} · registry status: {humanize((detail.registry_status[r.entry] || 'unknown').toLowerCase())}
                </T>
                <Row gap={6} style={{ marginTop: 6 }}>
                  {Object.entries(r.checks || {}).map(([k, ok]) => (
                    <Pill key={k} tone={ok ? 'ok' : 'bad'}>{`${ok ? '✓' : '✕'} ${humanize(k)}`}</Pill>
                  ))}
                </Row>
                {r.reasons.length ? (
                  <T v="tiny" style={{ marginTop: 4 }}>
                    {r.reasons.join('; ')}
                  </T>
                ) : null}
              </Card>
            ))
          ) : (
            <T v="small" color={c.muted}>
              No reused components: everything was built for this decision.
            </T>
          )}
        </>
      )}
    </View>
  );
}
