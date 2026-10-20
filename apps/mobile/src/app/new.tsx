/* Ask a new question: POST /v1/decisions, then build its world model and let futures branch (async jobs). */

import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, TextInput, View } from 'react-native';

import { Banner, Button, ErrorBox, Loading, Screen, Stack, T } from '@/components/ui';
import { api, waitJob } from '@/lib/api';
import { useSession } from '@/lib/session';
import { fonts, useTheme } from '@/lib/theme';
import type { JobRef } from '@/lib/types';

const pad = (n: number) => String(n).padStart(2, '0');
const local = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
function tzSuffix(): string {
  const o = -new Date().getTimezoneOffset();
  const a = Math.abs(o);
  return `${o >= 0 ? '+' : '-'}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}
/** "2026-10-19 09:00" → "2026-10-19T09:00:00-07:00" (local offset); null when malformed. */
function toIso(s: string): string | null {
  const m = /^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d)$/.exec(s.trim());
  return m ? `${m[1]}T${m[2]}:00${tzSuffix()}` : null;
}

export default function NewQuestion() {
  const { c } = useTheme();
  const { me } = useSession();
  const now = new Date();
  const [question, setQuestion] = useState('');
  const [alts, setAlts] = useState('Do it now\nWait a year');
  const [start, setStart] = useState(local(now));
  const [end, setEnd] = useState(local(new Date(now.getTime() + 365 * 864e5)));
  const [facts, setFacts] = useState('plan.budget_usd = 5000 USD\nplan.running_cost_usd_per_month = 300 USD/month');
  const [limits, setLimits] = useState('plan.budget_usd >= 0');
  const [metrics, setMetrics] = useState('budget_usd, budget_runway_months');
  const [advanced, setAdvanced] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<Error | null>(null);

  const input = {
    borderWidth: 1,
    borderColor: c.line2,
    borderRadius: 10,
    padding: 10,
    color: c.text,
    fontFamily: fonts.sans,
    fontSize: 15,
    backgroundColor: c.surface,
  } as const;
  const multi = { ...input, minHeight: 74, textAlignVertical: 'top' as const };

  const submit = async () => {
    if (!me) return;
    const s = toIso(start);
    const e = toIso(end);
    if (!question.trim()) return setErr(new Error('Please write your question.'));
    if (!s || !e) return setErr(new Error('Dates must look like 2026-10-19 09:00.'));
    setErr(null);
    const id = 'dec_' + Math.random().toString(36).slice(2, 10);
    const spec = {
      decision_id: id,
      question: question.trim(),
      owner_id: me.user_id,
      participants: [{ agent_id: 'me', user_id: me.user_id, display_name: me.display_name, profile_ref: me.profile_ids[0] || null }],
      participant_refs: ['me'],
      alternatives: alts
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
        .map((l) => ({
          id:
            l
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, '_')
              .replace(/^_|_$/g, '')
              .slice(0, 40) || 'option',
          policy: l,
          label: l,
        })),
      horizon: { starts_at: s, ends_at: e },
      initial_facts: facts
        .split('\n')
        .map((l) => l.trim())
        .filter((l) => l.includes('='))
        .map((l) => {
          const [ref, rest] = l.split('=').map((x) => x.trim());
          const [val, ...unit] = (rest || '').split(/\s+/);
          const num = Number(val);
          return { ref, value: Number.isNaN(num) ? val : num, unit: unit.join(' ') || (Number.isNaN(num) ? null : '1') };
        }),
      constraints: limits
        .split('\n')
        .map((l, i) => {
          const m = l.trim().match(/^(\S+)\s*>=\s*(-?[\d.]+)/);
          return m ? { id: `limit_${i}`, ref: m[1], min: Number(m[2]), hard: true, description: l.trim() } : null;
        })
        .filter(Boolean),
      metrics: metrics
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean),
      research_policy: { external_web: 'allowed' },
    };
    try {
      setStatus('Creating the world');
      await api.post('/v1/decisions', spec);
      setStatus('Researching and building a world model');
      await waitJob(await api.post<JobRef>(`/v1/decisions/${id}/models/compile`, { idempotency_key: `compile-${id}` }));
      setStatus('Letting futures branch');
      const plan = await api.post<{ experiment_id: string }>(`/v1/decisions/${id}/experiments`, {});
      await waitJob(await api.post<JobRef>(`/v1/experiments/${plan.experiment_id}/runs`, {}));
      setStatus(null);
      router.replace({ pathname: '/world/[id]', params: { id } });
    } catch (x) {
      setStatus(null);
      setErr(x as Error);
    }
  };

  return (
    <Screen maxWidth={720}>
      <T v="eyebrow">New world</T>
      <T v="h1" accessibilityRole="header" style={{ marginTop: 6 }}>
        Ask a question about your future
      </T>
      <T v="small" color={c.muted} style={{ marginTop: 6, marginBottom: 12 }}>
        Any kind of decision. The engine researches what it may, builds only what the decision needs, and says plainly what it cannot model.
      </T>
      <Stack>
        <T v="small">Your question</T>
        <TextInput
          value={question}
          onChangeText={setQuestion}
          placeholder="Should we … now, or wait?"
          placeholderTextColor={c.faint}
          style={input}
          accessibilityLabel="Your question"
        />
        <T v="small">Options you're weighing (one per line)</T>
        <TextInput value={alts} onChangeText={setAlts} multiline style={multi} accessibilityLabel="Options, one per line" />
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10 }}>
          <View style={{ flexGrow: 1, flexBasis: 200, gap: 6 }}>
            <T v="small">Futures start</T>
            <TextInput
              value={start}
              onChangeText={setStart}
              placeholder="2026-10-19 09:00"
              placeholderTextColor={c.faint}
              style={input}
              accessibilityLabel="Futures start, year-month-day hours:minutes"
            />
          </View>
          <View style={{ flexGrow: 1, flexBasis: 200, gap: 6 }}>
            <T v="small">…and run until</T>
            <TextInput
              value={end}
              onChangeText={setEnd}
              placeholder="2027-10-19 09:00"
              placeholderTextColor={c.faint}
              style={input}
              accessibilityLabel="Futures end, year-month-day hours:minutes"
            />
          </View>
        </View>
        <Pressable accessibilityRole="button" accessibilityState={{ expanded: advanced }} onPress={() => setAdvanced(!advanced)} style={{ paddingVertical: 6 }}>
          <T v="small" color={c.text}>
            {advanced ? '▾' : '▸'} What you already know (optional)
          </T>
        </Pressable>
        {advanced ? (
          <>
            <T v="small">Facts, one per line: thing.name = value unit</T>
            <TextInput
              value={facts}
              onChangeText={setFacts}
              multiline
              autoCapitalize="none"
              style={[multi, { fontFamily: fonts.mono, fontSize: 13 }]}
              accessibilityLabel="Facts"
            />
            <T v="small">Hard limits: thing.name &gt;= value</T>
            <TextInput
              value={limits}
              onChangeText={setLimits}
              multiline
              autoCapitalize="none"
              style={[multi, { fontFamily: fonts.mono, fontSize: 13 }]}
              accessibilityLabel="Hard limits"
            />
            <T v="small">What you'll judge outcomes by (comma separated)</T>
            <TextInput value={metrics} onChangeText={setMetrics} autoCapitalize="none" style={input} accessibilityLabel="Metrics" />
          </>
        ) : null}
        {err ? <ErrorBox error={err} /> : null}
        {status ? <Loading label={status} /> : null}
        <Button kind="primary" label="Build this world" onPress={submit} disabled={!!status} />
        <Banner tone="sim">Everything the engine simulates for this question is labelled as simulated. Counts of futures are coverage, never odds.</Banner>
      </Stack>
    </Screen>
  );
}
