/* Small UI kit. All text scales with the OS font size (Dynamic Type / browser zoom): nothing sets
 * allowFontScaling={false} and text containers never have fixed heights. */

import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Modal,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
  type PressableProps,
  type StyleProp,
  type TextProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { fmtTime, initials, TIERS, tierWords } from '@/lib/format';
import { fonts, useTheme } from '@/lib/theme';
import type { Tier } from '@/lib/types';

/* ------------------------------------------------------------------ text */
type Variant = 'display' | 'h1' | 'h2' | 'h3' | 'body' | 'lede' | 'small' | 'tiny' | 'eyebrow' | 'mono';

export function T({ v = 'body', color, style, ...rest }: TextProps & { v?: Variant; color?: string }) {
  const { c } = useTheme();
  const base: Record<Variant, TextStyle> = {
    display: { fontFamily: fonts.serif, fontSize: 28, lineHeight: 34, color: c.text },
    h1: { fontFamily: fonts.serif, fontSize: 24, lineHeight: 30, color: c.text },
    h2: { fontFamily: fonts.serif, fontSize: 20, lineHeight: 26, color: c.text, marginTop: 18, marginBottom: 8 },
    h3: { fontFamily: fonts.sans, fontSize: 16, lineHeight: 22, fontWeight: '600', color: c.text },
    body: { fontFamily: fonts.sans, fontSize: 15, lineHeight: 21, color: c.text },
    lede: { fontFamily: fonts.sans, fontSize: 15, lineHeight: 22, color: c.text2 },
    small: { fontFamily: fonts.sans, fontSize: 13, lineHeight: 18, color: c.text2 },
    tiny: { fontFamily: fonts.sans, fontSize: 11.5, lineHeight: 16, color: c.muted },
    eyebrow: { fontFamily: fonts.sans, fontSize: 11.5, lineHeight: 16, letterSpacing: 1.1, textTransform: 'uppercase', fontWeight: '600', color: c.muted },
    mono: { fontFamily: fonts.mono, fontSize: 12, lineHeight: 17, color: c.muted },
  };
  return <Text {...rest} style={[base[v], color ? { color } : null, style]} />;
}

/* ------------------------------------------------------------------ layout */
export function Screen({
  children,
  onRefresh,
  refreshing = false,
  maxWidth = 1120,
  padBottom = true,
}: {
  children: ReactNode;
  onRefresh?: () => void;
  refreshing?: boolean;
  maxWidth?: number;
  padBottom?: boolean;
}) {
  const { c } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: c.bg }}
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 14, paddingBottom: padBottom ? 40 + insets.bottom : 16 }}
      refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={c.present} /> : undefined}
      keyboardShouldPersistTaps="handled"
    >
      <View style={{ width: '100%', maxWidth, alignSelf: 'center' }}>{children}</View>
    </ScrollView>
  );
}

export function Row({
  children,
  gap = 8,
  wrap = true,
  style,
  between,
}: {
  children: ReactNode;
  gap?: number;
  wrap?: boolean;
  between?: boolean;
  style?: StyleProp<ViewStyle>;
}) {
  return (
    <View
      style={[{ flexDirection: 'row', alignItems: 'center', gap, flexWrap: wrap ? 'wrap' : 'nowrap' }, between && { justifyContent: 'space-between' }, style]}
    >
      {children}
    </View>
  );
}

export function Stack({ children, gap = 10, style }: { children: ReactNode; gap?: number; style?: StyleProp<ViewStyle> }) {
  return <View style={[{ gap }, style]}>{children}</View>;
}

export function Card({ children, tone, style }: { children: ReactNode; tone?: 'ground' | 'sim' | 'warn'; style?: StyleProp<ViewStyle> }) {
  const { c } = useTheme();
  const bg = tone === 'ground' ? c.groundBg : tone === 'sim' ? c.simBg : c.surface;
  const border = tone === 'ground' ? c.ground : tone === 'warn' ? c.warn : c.line;
  return (
    <View
      style={[
        {
          backgroundColor: bg,
          borderColor: tone ? border : c.line,
          borderWidth: StyleSheet.hairlineWidth * 2,
          borderRadius: 16,
          padding: 14,
          marginBottom: 12,
        },
        tone === 'ground' && { borderStyle: 'solid', borderColor: border + '55' },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/* ------------------------------------------------------------------ chips + badges */
type Tone = 'default' | 'warn' | 'bad' | 'ok' | 'sim' | 'ground';

export function Pill({ children, tone = 'default', color, style }: { children: ReactNode; tone?: Tone; color?: string; style?: StyleProp<ViewStyle> }) {
  const { c } = useTheme();
  const ink = color || { default: c.text2, warn: c.warn, bad: c.bad, ok: c.ok, sim: c.simInk, ground: c.ground }[tone];
  return (
    <View
      style={[
        {
          borderWidth: 1,
          borderColor: tone === 'default' && !color ? c.line2 : ink + '88',
          borderRadius: 999,
          paddingHorizontal: 9,
          paddingVertical: 2,
          alignSelf: 'flex-start',
        },
        tone === 'sim' && { backgroundColor: c.simBg },
        style,
      ]}
    >
      <Text style={{ color: ink, fontFamily: fonts.sans, fontSize: 12, lineHeight: 17, fontWeight: '500' }}>{children}</Text>
    </View>
  );
}

/** Every simulated value is labelled as simulated. */
export function SimStamp({ label = 'simulated' }: { label?: string }) {
  const { c } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel="Simulated — not a real-world event"
      style={{
        borderWidth: 1,
        borderStyle: 'dashed',
        borderColor: c.simInk,
        borderRadius: 6,
        paddingHorizontal: 6,
        paddingVertical: 1,
        alignSelf: 'flex-start',
        backgroundColor: c.simBg,
      }}
    >
      <Text style={{ color: c.simInk, fontFamily: fonts.mono, fontSize: 10.5, lineHeight: 15, letterSpacing: 1, textTransform: 'uppercase' }}>{label}</Text>
    </View>
  );
}

/** Four-step evidence-support meter. Never a percentage, never averaged, never a likelihood. */
export function TierMeter({ tier, withWords }: { tier: Tier | string | null | undefined; withWords?: boolean }) {
  const { c } = useTheme();
  const n = tier ? (TIERS[tier as Tier]?.n ?? 0) : 0;
  const col = n >= 4 ? c.ok : n === 3 ? c.ground : n === 2 ? c.warn : c.bad;
  return (
    <View
      accessible
      accessibilityLabel={`${tierWords(tier)}. Evidence support, not forecast accuracy.`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}
    >
      <View style={{ flexDirection: 'row', gap: 2 }}>
        {[1, 2, 3, 4].map((i) => (
          <View key={i} style={{ width: 6, height: 10, borderRadius: 1.5, backgroundColor: i <= n ? col : c.line2 }} />
        ))}
      </View>
      {withWords ? <T v="small">{tierWords(tier)}</T> : null}
    </View>
  );
}

export function Avatar({ name, index, size = 26 }: { name: string; index: number; size?: number }) {
  const { c, person } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel={name}
      style={{
        width: size,
        height: size,
        borderRadius: size / 2,
        backgroundColor: person(index),
        alignItems: 'center',
        justifyContent: 'center',
        borderWidth: 2,
        borderColor: c.bg,
      }}
    >
      <Text style={{ color: '#151515', fontSize: size * 0.38, fontWeight: '700', fontFamily: fonts.sans }}>{initials(name)}</Text>
    </View>
  );
}

export function Dot({ color, size = 9 }: { color: string; size?: number }) {
  return <View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: color }} />;
}

/* ------------------------------------------------------------------ actions */
export function Button({
  label,
  onPress,
  kind = 'default',
  small,
  disabled,
  style,
  accessibilityLabel,
  ...rest
}: {
  label: string;
  onPress?: () => void;
  kind?: 'default' | 'primary' | 'ghost' | 'danger';
  small?: boolean;
  disabled?: boolean;
  style?: StyleProp<ViewStyle>;
} & Omit<PressableProps, 'style' | 'children'>) {
  const { c } = useTheme();
  const bg = kind === 'primary' ? c.accent : kind === 'ghost' ? 'transparent' : c.surface2;
  const ink = kind === 'primary' ? c.accentInk : kind === 'danger' ? c.bad : c.text;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel || label}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        {
          backgroundColor: bg,
          borderWidth: 1,
          borderColor: kind === 'primary' ? c.accent : c.line2,
          borderRadius: 999,
          paddingHorizontal: small ? 12 : 16,
          paddingVertical: small ? 6 : 10,
          minHeight: small ? 34 : 44,
          justifyContent: 'center',
          alignItems: 'center',
          opacity: disabled ? 0.45 : pressed ? 0.75 : 1,
          alignSelf: 'flex-start',
        },
        style,
      ]}
      {...rest}
    >
      <Text style={{ color: ink, fontFamily: fonts.sans, fontSize: small ? 13 : 15, fontWeight: '600', textAlign: 'center' }}>{label}</Text>
    </Pressable>
  );
}

export function LinkText({ children, onPress, color }: { children: ReactNode; onPress: () => void; color?: string }) {
  const { c } = useTheme();
  return (
    <Text accessibilityRole="link" onPress={onPress} style={{ color: color || c.present, fontFamily: fonts.sans, fontSize: 14, fontWeight: '500' }}>
      {children}
    </Text>
  );
}

/** Segmented control / tab strip. Scrolls horizontally when labels don't fit. */
export function Segmented<K extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { key: K; label: string }[];
  value: K;
  onChange: (k: K) => void;
  label: string;
}) {
  const { c } = useTheme();
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      accessibilityRole="tablist"
      accessibilityLabel={label}
      contentContainerStyle={{ gap: 6, paddingVertical: 2 }}
    >
      {options.map((o) => {
        const on = o.key === value;
        return (
          <Pressable
            key={o.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: on }}
            onPress={() => onChange(o.key)}
            style={{
              paddingHorizontal: 14,
              paddingVertical: 8,
              minHeight: 38,
              justifyContent: 'center',
              borderRadius: 999,
              borderWidth: 1,
              borderColor: on ? c.present : c.line,
              backgroundColor: on ? c.surface2 : 'transparent',
            }}
          >
            <Text style={{ color: on ? c.text : c.muted, fontFamily: fonts.sans, fontSize: 14, fontWeight: on ? '600' : '500' }}>{o.label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

/* ------------------------------------------------------------------ status */
export function Banner({ children, tone = 'sim', style }: { children: ReactNode; tone?: 'sim' | 'warn' | 'bad' | 'ok'; style?: StyleProp<ViewStyle> }) {
  const { c } = useTheme();
  const col = { sim: c.simInk, warn: c.warn, bad: c.bad, ok: c.ok }[tone];
  return (
    <View
      accessibilityRole={tone === 'bad' ? 'alert' : undefined}
      style={[
        {
          borderLeftWidth: 3,
          borderLeftColor: col,
          backgroundColor: col + '14',
          borderRadius: 10,
          paddingHorizontal: 12,
          paddingVertical: 9,
          marginBottom: 10,
        },
        style,
      ]}
    >
      {typeof children === 'string' ? (
        <T v="small" color={c.text}>
          {children}
        </T>
      ) : (
        children
      )}
    </View>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  const { c } = useTheme();
  return (
    <Row style={{ paddingVertical: 14 }}>
      <ActivityIndicator color={c.present} />
      <T v="small" color={c.muted}>
        {label}…
      </T>
    </Row>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error | string; onRetry?: () => void }) {
  return (
    <Banner tone="bad">
      <T v="small">{typeof error === 'string' ? error : error.message}</T>
      {onRetry ? <Button small label="Try again" onPress={onRetry} style={{ marginTop: 8 }} /> : null}
    </Banner>
  );
}

/** Shown when a screen is rendering results saved on this device because the server is unreachable. */
export function OfflineNote({ cachedAt }: { cachedAt: string | null }) {
  if (!cachedAt) return null;
  return (
    <Banner tone="warn">
      {`Offline — showing results saved on this device (${fmtTime(cachedAt, 'minute', true)}). Actions are unavailable until the server is reachable.`}
    </Banner>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  const { c } = useTheme();
  return (
    <Card>
      <T v="body" color={c.muted} style={{ textAlign: 'center', paddingVertical: 10 }}>
        {children}
      </T>
    </Card>
  );
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  const { c } = useTheme();
  return (
    <View>
      {rows.map(([k, v], i) => (
        <View
          key={i}
          style={{
            flexDirection: 'row',
            gap: 12,
            paddingVertical: 7,
            borderTopWidth: i ? StyleSheet.hairlineWidth : 0,
            borderTopColor: c.line,
            flexWrap: 'wrap',
          }}
        >
          <View style={{ flex: 1, minWidth: 140 }}>
            {typeof k === 'string' ? (
              <T v="small" color={c.muted}>
                {k}
              </T>
            ) : (
              k
            )}
          </View>
          <View style={{ flexShrink: 1, alignItems: 'flex-end', maxWidth: '100%' }}>
            {typeof v === 'string' ? (
              <T v="small" style={{ textAlign: 'right' }}>
                {v}
              </T>
            ) : (
              v
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

/* ------------------------------------------------------------------ sheet */
export function Sheet({ visible, onClose, title, children }: { visible: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const { c, reduceMotion } = useTheme();
  const insets = useSafeAreaInsets();
  return (
    <Modal visible={visible} transparent animationType={reduceMotion ? 'none' : 'slide'} onRequestClose={onClose}>
      <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)', justifyContent: 'flex-end' }}>
        <Pressable accessibilityLabel="Close" style={StyleSheet.absoluteFill} onPress={onClose} />
        <View
          accessibilityViewIsModal
          style={{
            backgroundColor: c.surface,
            borderTopLeftRadius: 20,
            borderTopRightRadius: 20,
            maxHeight: '88%',
            width: '100%',
            maxWidth: 720,
            alignSelf: 'center',
            borderWidth: 1,
            borderColor: c.line,
            paddingBottom: insets.bottom,
          }}
        >
          <Row between style={{ paddingHorizontal: 16, paddingTop: 14, paddingBottom: 6 }} wrap={false}>
            <T v="h3" style={{ flex: 1 }} accessibilityRole="header">
              {title}
            </T>
            <Button small kind="ghost" label="Close" onPress={onClose} />
          </Row>
          <ScrollView contentContainerStyle={{ padding: 16, paddingTop: 6 }}>{children}</ScrollView>
        </View>
      </View>
    </Modal>
  );
}
