import { router, Tabs } from 'expo-router';
import { Pressable, Text, type ColorValue } from 'react-native';

import { useSession } from '@/lib/session';
import { fonts, useTheme } from '@/lib/theme';

function Glyph({ g, color }: { g: string; color: ColorValue }) {
  return (
    <Text accessible={false} style={{ color, fontSize: 18, lineHeight: 22 }}>
      {g}
    </Text>
  );
}

function Who() {
  const { c } = useTheme();
  const { me } = useSession();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`Signed in as ${me?.display_name ?? 'nobody'}. Switch participant`}
      onPress={() => router.push('/login')}
      style={{ marginRight: 12, borderWidth: 1, borderColor: c.line, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6, maxWidth: 200 }}
    >
      <Text numberOfLines={1} style={{ color: c.text, fontFamily: fonts.sans, fontSize: 13 }}>
        {me?.display_name ?? 'Sign in'}
      </Text>
    </Pressable>
  );
}

export default function TabsLayout() {
  const { c } = useTheme();
  return (
    <Tabs
      screenOptions={{
        headerStyle: { backgroundColor: c.surface },
        headerTintColor: c.text,
        headerTitleStyle: { fontFamily: fonts.serif, color: c.text },
        headerRight: () => <Who />,
        tabBarStyle: { backgroundColor: c.surface, borderTopColor: c.line },
        tabBarActiveTintColor: c.present,
        tabBarInactiveTintColor: c.muted,
        sceneStyle: { backgroundColor: c.bg },
      }}
    >
      <Tabs.Screen name="index" options={{ title: 'Worlds', tabBarIcon: ({ color }) => <Glyph g="◎" color={color} /> }} />
      <Tabs.Screen name="agent" options={{ title: 'Your Agent', tabBarIcon: ({ color }) => <Glyph g="◐" color={color} /> }} />
      <Tabs.Screen name="integrity" options={{ title: 'Integrity', tabBarIcon: ({ color }) => <Glyph g="⛨" color={color} /> }} />
    </Tabs>
  );
}
