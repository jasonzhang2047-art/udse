/* Accessible scrubber: drag the track, or use the step buttons (keyboard, switch access, screen readers). */

import { useMemo, useRef, useState } from 'react';
import { PanResponder, View } from 'react-native';

import { useTheme } from '@/lib/theme';

import { Button, Row } from './ui';

export function Scrubber({
  value,
  min,
  max,
  onChange,
  onCommit,
  label,
  valueText,
  steps = 60,
}: {
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
  /** Called when a drag ends or a step button is used (e.g. to fetch state for the new position). */
  onCommit?: (v: number) => void;
  label: string;
  valueText: string;
  /** Number of increments the step buttons divide the range into. */
  steps?: number;
}) {
  const { c } = useTheme();
  const [width, setWidth] = useState(1);
  const start = useRef(0);
  const last = useRef(value);
  const range = Math.max(1e-9, max - min);
  const stepSize = range / Math.max(1, steps);
  const frac = Math.min(1, Math.max(0, (value - min) / range));

  const clamp = (v: number) => Math.min(max, Math.max(min, v));
  const fromX = (x: number) => clamp(min + (Math.min(width, Math.max(0, x)) / width) * range);

  // Refs keep the responder stable while props change during a drag.
  const live = useRef({ fromX, onChange, onCommit });
  live.current = { fromX, onChange, onCommit };
  const pan = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: () => true,
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (e) => {
          start.current = e.nativeEvent.locationX;
          last.current = live.current.fromX(start.current);
          live.current.onChange(last.current);
        },
        onPanResponderMove: (_, g) => {
          last.current = live.current.fromX(start.current + g.dx);
          live.current.onChange(last.current);
        },
        onPanResponderRelease: () => live.current.onCommit?.(last.current),
        onPanResponderTerminate: () => live.current.onCommit?.(last.current),
      }),
    [],
  );

  const stepBy = (d: number) => {
    const v = clamp(value + d * stepSize);
    onChange(v);
    onCommit?.(v);
  };

  return (
    <Row wrap={false} gap={8}>
      <Button small label="◀" accessibilityLabel={`Step back. ${label}`} onPress={() => stepBy(-1)} disabled={value <= min} />
      <View
        accessible
        accessibilityRole="adjustable"
        accessibilityLabel={label}
        accessibilityValue={{ text: valueText }}
        accessibilityActions={[{ name: 'increment' }, { name: 'decrement' }]}
        onAccessibilityAction={(e) => stepBy(e.nativeEvent.actionName === 'increment' ? 1 : -1)}
        onLayout={(e) => setWidth(Math.max(1, e.nativeEvent.layout.width))}
        style={{ flex: 1, height: 36, justifyContent: 'center' }}
        {...pan.panHandlers}
      >
        <View pointerEvents="none" style={{ height: 4, borderRadius: 2, backgroundColor: c.line2 }}>
          <View style={{ width: `${frac * 100}%`, height: 4, borderRadius: 2, backgroundColor: c.present }} />
        </View>
        <View
          pointerEvents="none"
          style={{
            position: 'absolute',
            left: frac * width - 11,
            width: 22,
            height: 22,
            borderRadius: 11,
            backgroundColor: c.surface,
            borderWidth: 3,
            borderColor: c.present,
          }}
        />
      </View>
      <Button small label="▶" accessibilityLabel={`Step forward. ${label}`} onPress={() => stepBy(1)} disabled={value >= max} />
    </Row>
  );
}
