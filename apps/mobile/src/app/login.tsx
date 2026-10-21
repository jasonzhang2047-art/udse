/* Development sign-in: pick a synthetic fixture participant (GET /v1/dev/users). */

import { router } from 'expo-router';
import { useState } from 'react';
import { Platform, Pressable, TextInput, View } from 'react-native';

import { Avatar, Banner, Button, Card, ErrorBox, Loading, OfflineNote, Row, Screen, T } from '@/components/ui';
import { clearCache } from '@/lib/api';
import { useSession } from '@/lib/session';
import { fonts, useTheme, type ThemeMode } from '@/lib/theme';
import type { DevUser, DevUsers } from '@/lib/types';
import { useApi } from '@/lib/useApi';

const GROUP_NAMES: Record<string, string> = {
  fundraising: 'Four founders · raise now or later',
  restaurant: 'Four friends · where to eat tonight',
  new_domain: 'Community garden & other clean-room questions',
};

export default function Login() {
  const { c, mode, setMode } = useTheme();
  const { signIn, me, status, baseUrl, setBaseUrl } = useSession();
  const users = useApi<DevUsers>('/v1/dev/users');
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<Error | null>(null);
  const [url, setUrl] = useState(baseUrl);

  const groups: Record<string, DevUser[]> = {};
  users.data?.users.forEach((u) => (groups[u.fixture] = groups[u.fixture] || []).push(u));

  const pick = async (u: DevUser) => {
    setBusy(u.user_id);
    setErr(null);
    try {
      await signIn(u.token);
      router.replace('/');
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(null);
    }
  };

  return (
    // Signed out, this screen has no header, so it must clear the status bar / notch itself.
    <Screen maxWidth={900} safeTop={status !== 'signedIn'}>
      <T v="eyebrow">Development sign-in</T>
      <T v="display" accessibilityRole="header" style={{ marginTop: 6 }}>
        Whose futures are we exploring?
      </T>
      <T v="lede" style={{ marginTop: 8, marginBottom: 14 }}>
        Pick a synthetic participant. Each person sees only their own Agent profile; worlds are shared with everyone in them.
      </T>
      {me ? <Banner tone="ok">{`Signed in as ${me.display_name}. Pick someone else to switch.`}</Banner> : null}
      <OfflineNote cachedAt={users.cachedAt} />
      {users.loading && !users.data ? <Loading label="Finding fixture participants" /> : null}
      {users.error ? <ErrorBox error={users.error} onRetry={users.reload} /> : null}
      {err ? <ErrorBox error={err} /> : null}
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
        {Object.entries(groups).map(([g, us]) => (
          <Card key={g} style={{ flexGrow: 1, flexBasis: 260 }}>
            <T v="eyebrow">{GROUP_NAMES[g] || g}</T>
            <View style={{ marginTop: 10, gap: 8 }}>
              {us.map((u, i) => (
                <Pressable
                  key={u.user_id}
                  accessibilityRole="button"
                  accessibilityLabel={`Sign in as ${u.display_name}`}
                  disabled={!!busy}
                  onPress={() => pick(u)}
                  style={({ pressed }) => ({
                    flexDirection: 'row',
                    alignItems: 'center',
                    gap: 10,
                    padding: 10,
                    minHeight: 48,
                    borderRadius: 12,
                    borderWidth: 1,
                    borderColor: me?.user_id === u.user_id ? c.present : c.line,
                    backgroundColor: pressed ? c.surface2 : 'transparent',
                  })}
                >
                  <Avatar name={u.display_name} index={i} />
                  <T v="body" style={{ flex: 1 }}>
                    {u.display_name}
                  </T>
                  {busy === u.user_id ? <T v="tiny">signing in…</T> : null}
                </Pressable>
              ))}
            </View>
          </Card>
        ))}
      </View>
      {users.data ? (
        <T v="tiny" style={{ marginTop: 4 }}>
          {users.data.warning} Production sign-in is out of scope for this reference build.
        </T>
      ) : null}

      <T v="h2">Settings</T>
      <Card>
        <T v="small" color={c.muted}>
          Appearance
        </T>
        <Row style={{ marginTop: 8 }}>
          {(['auto', 'dark', 'light'] as ThemeMode[]).map((m) => (
            <Button
              key={m}
              small
              kind={mode === m ? 'primary' : 'default'}
              label={{ auto: 'Match system', dark: 'Observatory (dark)', light: 'Archive (light)' }[m]}
              onPress={() => setMode(m)}
            />
          ))}
        </Row>
        <T v="small" color={c.muted} style={{ marginTop: 16 }}>
          UDSE server
        </T>
        <T v="tiny">
          {Platform.OS === 'web'
            ? 'Leave empty to use this origin (the Expo dev server proxies /v1 to the backend). Another origin needs `udse serve --cors-origin`.'
            : 'Simulators reach your machine at 127.0.0.1 (iOS) or 10.0.2.2 (Android). On a phone, use your computer’s LAN address and start the backend with --host 0.0.0.0.'}
        </T>
        <TextInput
          value={url}
          onChangeText={setUrl}
          placeholder={Platform.OS === 'web' ? '(same origin)' : 'http://127.0.0.1:8765'}
          placeholderTextColor={c.faint}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          accessibilityLabel="UDSE server address"
          style={{ marginTop: 8, borderWidth: 1, borderColor: c.line2, borderRadius: 10, padding: 10, color: c.text, fontFamily: fonts.mono, fontSize: 14 }}
        />
        <Row style={{ marginTop: 10 }}>
          <Button
            small
            label="Use this server"
            onPress={async () => {
              await setBaseUrl(url);
              users.reload();
            }}
          />
          <Button small kind="ghost" label="Clear saved results" onPress={() => clearCache()} />
        </Row>
      </Card>
    </Screen>
  );
}
