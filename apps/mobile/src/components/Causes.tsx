/* "Why did this happen?" (GET /v1/runs/{id}/why/{seq}) and the epistemic-boundary demonstration
 * (GET /v1/runs/{id}/events/{seq}/promotion-check). Both read committed events only. */

import { useState } from 'react';
import { View } from 'react-native';

import { api } from '@/lib/api';
import { fmtTime } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { PromotionCheck, Why } from '@/lib/types';

import { Banner, Button, ErrorBox, Loading, Row, T, TierMeter } from './ui';

export function WhyChain({ why, res }: { why: Why; res: string }) {
  const { c } = useTheme();
  return (
    <View style={{ marginTop: 8, borderLeftWidth: 2, borderLeftColor: c.line2, paddingLeft: 10, gap: 8 }}>
      {why.chain.map((s, i) => (
        <View key={i}>
          <T v="mono">{fmtTime(s.sim_at, res, true)}</T>
          <T v="small" color={c.text}>
            {s.headline || s.text.replace(/^At simulated \S+: /, '')}
          </T>
          {s.rationale ? <T v="tiny">Agent reasoning (simulated): “{(s.reasoning || s.rationale).slice(0, 240)}”</T> : null}
          {(s.mechanisms || []).map((m) => (
            <Row key={m.id} gap={6} style={{ marginTop: 2 }}>
              <TierMeter tier={m.tier} />
              <T v="tiny" style={{ flex: 1 }}>
                {m.description || m.id}
              </T>
            </Row>
          ))}
          {(s.random_draws || []).map((d, j) => (
            <T key={j} v="tiny">
              Recorded random draw ({d.stream}): {JSON.stringify(d.value)}
            </T>
          ))}
        </View>
      ))}
      {why.limitations.slice(0, 1).map((l) => (
        <T key={l} v="tiny">
          {l}
        </T>
      ))}
    </View>
  );
}

export function PromotionResult({ r }: { r: PromotionCheck }) {
  return (
    <View style={{ marginTop: 8 }}>
      <Banner tone={r.all_refused ? 'ok' : 'bad'}>
        {r.all_refused
          ? `All ${r.attempts.length} ways of turning this simulated event into real evidence or profile evidence were refused. Shared knowledge unchanged: ${r.layer_a_hash_unchanged ? 'yes' : 'NO'}.`
          : 'A promotion path was accepted — boundary breach.'}
      </Banner>
      {r.attempts.map((a) => (
        <T key={a.path} v="tiny">
          {a.refused ? '✕ refused' : '✓ ACCEPTED'} · {a.path}
        </T>
      ))}
    </View>
  );
}

/** Buttons that load the causal chain and run the promotion check for one committed event. */
export function EventActions({ runId, seq, res }: { runId: string; seq: number; res: string }) {
  const [why, setWhy] = useState<Why | null>(null);
  const [promo, setPromo] = useState<PromotionCheck | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const run = async (kind: 'why' | 'promo') => {
    setBusy(kind);
    setErr(null);
    try {
      if (kind === 'why') setWhy(await api.get<Why>(`/v1/runs/${runId}/why/${seq}`));
      else setPromo(await api.get<PromotionCheck>(`/v1/runs/${runId}/events/${seq}/promotion-check`));
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(null);
    }
  };
  return (
    <View style={{ marginTop: 10 }}>
      <Row>
        <Button small label={why ? 'Hide causes' : 'Why did this happen?'} onPress={() => (why ? setWhy(null) : run('why'))} />
        <Button small kind="ghost" label="Try to treat it as real evidence" onPress={() => run('promo')} />
      </Row>
      {busy ? <Loading label={busy === 'why' ? 'Tracing causes' : 'Attempting every promotion path'} /> : null}
      {err ? <ErrorBox error={err} /> : null}
      {why ? <WhyChain why={why} res={res} /> : null}
      {promo ? <PromotionResult r={promo} /> : null}
    </View>
  );
}
