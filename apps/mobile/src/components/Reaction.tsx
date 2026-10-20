/* "Your reaction": the only way viewing a simulated path can become evidence about the person — an explicit
 * statement (POST /v1/human-evidence). Looking at, scrubbing or comparing paths is never read as preference. */

import { router } from 'expo-router';
import { useState } from 'react';
import { Switch, TextInput, View } from 'react-native';

import { api } from '@/lib/api';
import { fonts, useTheme } from '@/lib/theme';

import { Banner, Button, ErrorBox, LinkText, Row, Sheet, Stack, T } from './ui';

interface Result {
  stored: boolean;
  reason?: string;
  proposal: { proposal_id: string; mode: string } | null;
}

export function Reaction({ decisionId, runIds, pathLabel }: { decisionId: string; runIds: string[]; pathLabel: string }) {
  const { c } = useTheme();
  const [out, setOut] = useState<Result | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [statement, setStatement] = useState('');
  const [key, setKey] = useState('');
  const [value, setValue] = useState('');
  const [hard, setHard] = useState(false);

  const send = async (signal: 'EXPLICIT_SELECTION' | 'EXPLICIT_CORRECTION', payload: Record<string, unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      setOut(
        await api.post<Result>('/v1/human-evidence', {
          decision_id: decisionId,
          signal_type: signal,
          payload,
          referenced_run_ids: runIds,
          consent: { explicit_feedback: true },
        }),
      );
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  const submitCorrection = () => {
    let parsed: unknown = value || null;
    try {
      parsed = value ? JSON.parse(value) : null;
    } catch {
      // keep the text as written
    }
    setOpen(false);
    void send('EXPLICIT_CORRECTION', { statement, feature_key: key || null, value: parsed, hard });
  };

  const input = { borderWidth: 1, borderColor: c.line2, borderRadius: 10, padding: 10, color: c.text, fontFamily: fonts.sans, fontSize: 15 } as const;

  return (
    <View>
      <T v="tiny">Only what you explicitly say counts as evidence about you. Looking at a path is not read as preference.</T>
      <Row style={{ marginTop: 8 }}>
        <Button
          small
          label="This path matters to me"
          disabled={busy}
          onPress={() => send('EXPLICIT_SELECTION', { statement: 'This path matters to me', preferred_path: pathLabel })}
        />
        <Button small kind="ghost" label="My Agent wouldn't do that…" disabled={busy} onPress={() => setOpen(true)} />
      </Row>
      {err ? <ErrorBox error={err} /> : null}
      {out ? (
        <Banner tone="sim" style={{ marginTop: 10 }}>
          <T v="small" color={c.text}>
            {out.stored ? 'Recorded as your own evidence.' : `Not stored: ${out.reason}`}{' '}
            {out.proposal ? (
              <>
                A suggested profile update is waiting for your approval in <LinkText onPress={() => router.push('/agent')}>Your Agent</LinkText>.
              </>
            ) : out.stored ? (
              'No profile change implied.'
            ) : null}
          </T>
        </Banner>
      ) : null}
      <Sheet visible={open} onClose={() => setOpen(false)} title="Correct how your Agent represents you">
        <Stack>
          <T v="small" color={c.muted}>
            This becomes a suggested update to your real profile. Nothing changes until you approve it.
          </T>
          <T v="small">In your own words</T>
          <TextInput
            value={statement}
            onChangeText={setStatement}
            placeholder="e.g. I will not accept terms that remove founder control"
            placeholderTextColor={c.faint}
            style={input}
            accessibilityLabel="Your correction, in your own words"
          />
          <T v="small">Structured feature (optional)</T>
          <TextInput
            value={key}
            onChangeText={setKey}
            placeholder="constraint.founder_control_retained"
            placeholderTextColor={c.faint}
            autoCapitalize="none"
            style={[input, { fontFamily: fonts.mono, fontSize: 13 }]}
            accessibilityLabel="Feature key"
          />
          <TextInput
            value={value}
            onChangeText={setValue}
            placeholder='{"op": "==", "value": true}'
            placeholderTextColor={c.faint}
            autoCapitalize="none"
            style={[input, { fontFamily: fonts.mono, fontSize: 13 }]}
            accessibilityLabel="Feature value as JSON"
          />
          <Row>
            <Switch value={hard} onValueChange={setHard} accessibilityLabel="This is a hard boundary for me" />
            <T v="small">This is a hard boundary for me</T>
          </Row>
          <Button kind="primary" label="Send correction" disabled={!statement.trim()} onPress={submitCorrection} />
        </Stack>
      </Sheet>
    </View>
  );
}
