import { DarkTheme, DefaultTheme, Stack, ThemeProvider as NavThemeProvider } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { Loading } from '@/components/ui';
import { SessionProvider, useSession } from '@/lib/session';
import { fonts, ThemeProvider, useTheme } from '@/lib/theme';

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <ThemeProvider>
        <SessionProvider>
          <Shell />
        </SessionProvider>
      </ThemeProvider>
    </SafeAreaProvider>
  );
}

function Shell() {
  const { c, isDark, reduceMotion } = useTheme();
  const { status } = useSession();
  const base = isDark ? DarkTheme : DefaultTheme;
  const navTheme = {
    ...base,
    colors: { ...base.colors, background: c.bg, card: c.surface, text: c.text, border: c.line, primary: c.present, notification: c.present },
  };

  if (status === 'loading') {
    return (
      <View style={{ flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center' }}>
        <Loading label="Starting" />
      </View>
    );
  }

  return (
    <NavThemeProvider value={navTheme}>
      <StatusBar style={isDark ? 'light' : 'dark'} />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: c.surface },
          headerTintColor: c.text,
          headerTitleStyle: { fontFamily: fonts.serif, color: c.text },
          contentStyle: { backgroundColor: c.bg },
          animation: reduceMotion ? 'none' : 'default',
        }}
      >
        <Stack.Protected guard={status === 'signedIn'}>
          <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
          {/* These two screens have horizontal scrubbers. iOS 26+ pops a screen on a rightward pan that starts
           * anywhere, which would take the drag away from the scrubber, so back stays an edge swipe here. */}
          <Stack.Screen name="world/[id]" options={{ title: 'World', fullScreenGestureEnabled: false }} />
          <Stack.Screen name="run/[id]" options={{ title: 'Path timeline', fullScreenGestureEnabled: false }} />
          <Stack.Screen name="compare/[a]/[b]" options={{ title: 'Where two futures part' }} />
          <Stack.Screen name="new" options={{ title: 'New question', presentation: 'modal' }} />
        </Stack.Protected>
        <Stack.Screen name="login" options={{ title: 'Sign in', headerShown: status === 'signedIn' }} />
      </Stack>
    </NavThemeProvider>
  );
}
