/* Visual system (docs/design/ui-redesign.md §7): dark "observatory" and light "archive" themes.
 * Real evidence uses warm, solid "ground" tones; simulated futures use luminous outcome hues. */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { AccessibilityInfo, Platform, useColorScheme } from 'react-native';

const dark = {
  bg: '#0a0c11',
  bg2: '#0d1017',
  surface: '#11141b',
  surface2: '#181c25',
  surface3: '#20252f',
  line: '#262b36',
  line2: '#343a47',
  text: '#ece6d9',
  text2: '#c9c2b4',
  muted: '#958e80',
  faint: '#6b6559',
  present: '#f5a524',
  ground: '#cdb68b',
  groundBg: 'rgba(205,182,139,0.08)',
  ghost: '#5b6170',
  cut: '#e0675f',
  ok: '#6ad3a2',
  warn: '#e9b44c',
  bad: '#ef7b72',
  simBg: 'rgba(140,150,255,0.10)',
  simInk: '#b5bcff',
  accent: '#f5a524',
  accentInk: '#1b1305',
  onOutcome: '#0b0d12',
  outcomes: ['#5eead4', '#a78bfa', '#fb7185', '#fbbf24', '#60a5fa', '#a3e635', '#f472b6', '#38bdf8'],
  people: ['#c7b3e6', '#9fc9b8', '#e6b3a3', '#a9bbe0', '#d9cf9a', '#b8d49a'],
};

type Palette = typeof dark;

const light: Palette = {
  bg: '#f3eee4',
  bg2: '#efe8db',
  surface: '#fbf8f1',
  surface2: '#f2ecdf',
  surface3: '#e9e1d1',
  line: '#dcd2c0',
  line2: '#cbbfa9',
  text: '#1d1913',
  text2: '#3d362b',
  muted: '#6c6355',
  faint: '#988e7d',
  present: '#b5650d',
  ground: '#7a6230',
  groundBg: 'rgba(122,98,48,0.07)',
  ghost: '#a39a8a',
  cut: '#b23a32',
  ok: '#1d7a4f',
  warn: '#9a6a07',
  bad: '#b23a32',
  simBg: 'rgba(70,80,190,0.08)',
  simInk: '#3c45a6',
  accent: '#b5650d',
  accentInk: '#fff8ee',
  onOutcome: '#fffdf8',
  outcomes: ['#0f766e', '#6d28d9', '#be123c', '#a16207', '#1d4ed8', '#4d7c0f', '#be185d', '#0369a1'],
  people: ['#7c5ea8', '#3f7d66', '#a5604b', '#4e6597', '#857a36', '#5c7d36'],
};

/* System font stacks only: the app stays fully usable offline without web fonts. */
export const fonts = {
  serif: Platform.select({ ios: 'Georgia', android: 'serif', default: '"Iowan Old Style", "Palatino Linotype", Georgia, serif' }),
  sans: Platform.select({ ios: 'System', android: 'sans-serif', default: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' }),
  mono: Platform.select({ ios: 'Menlo', android: 'monospace', default: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' }),
};

export type ThemeMode = 'auto' | 'dark' | 'light';

interface ThemeValue {
  c: Palette;
  isDark: boolean;
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
  reduceMotion: boolean;
  outcome: (i: number) => string;
  person: (i: number) => string;
}

const ThemeContext = createContext<ThemeValue | null>(null);
const THEME_KEY = 'udse:theme';

export function ThemeProvider({ children }: { children: ReactNode }) {
  const scheme = useColorScheme();
  const [mode, setModeState] = useState<ThemeMode>('auto');
  const reduceMotion = useReducedMotion();

  useEffect(() => {
    AsyncStorage.getItem(THEME_KEY)
      .then((m) => {
        if (m === 'dark' || m === 'light' || m === 'auto') setModeState(m);
      })
      .catch(() => {});
  }, []);

  const value = useMemo<ThemeValue>(() => {
    const isDark = mode === 'auto' ? scheme !== 'light' : mode === 'dark';
    const c = isDark ? dark : light;
    return {
      c,
      isDark,
      mode,
      reduceMotion,
      setMode: (m) => {
        setModeState(m);
        AsyncStorage.setItem(THEME_KEY, m).catch(() => {});
      },
      outcome: (i) => c.outcomes[((i % 8) + 8) % 8],
      person: (i) => c.people[((i % 6) + 6) % 6],
    };
  }, [mode, scheme, reduceMotion]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const v = useContext(ThemeContext);
  if (!v) throw new Error('useTheme outside ThemeProvider');
  return v;
}

/** OS "reduce motion" setting (prefers-reduced-motion on the web). */
export function useReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => alive && setReduce(v))
      .catch(() => {});
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduce);
    return () => {
      alive = false;
      sub?.remove?.();
    };
  }, []);
  return reduce;
}
