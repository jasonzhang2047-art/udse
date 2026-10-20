/* Setup: the question as asked — options, facts, hard limits and scope. */

import { View } from 'react-native';

import { Card, KV, Pill, T } from '@/components/ui';
import { fmtVal, humanize } from '@/lib/format';
import { useTheme } from '@/lib/theme';
import type { DecisionSpec, Scope } from '@/lib/types';

export function SetupTab({ spec, scope }: { spec: DecisionSpec; scope: Scope }) {
  const { c } = useTheme();
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
      <Card style={{ flexGrow: 1, flexBasis: 300 }}>
        <T v="eyebrow">Options being weighed</T>
        {scope.alternatives.map((a) => (
          <View key={a.id} style={{ marginTop: 10 }}>
            <T v="body" style={{ fontWeight: '600' }}>
              {a.label || a.id}
            </T>
            {a.suggested_by_system ? <Pill tone="warn">suggested by the system</Pill> : null}
            <T v="small" color={c.muted}>
              {a.description || a.policy}
            </T>
            {(a.assumptions || []).map((x, i) => (
              <T key={i} v="small">
                • {x.description || x.ref} {x.at ? `(at ${x.at})` : ''}
              </T>
            ))}
          </View>
        ))}
      </Card>
      <Card style={{ flexGrow: 1, flexBasis: 300 }}>
        <T v="eyebrow">What you told us</T>
        <View style={{ marginTop: 6 }}>
          {spec.initial_facts.length ? (
            <KV
              rows={spec.initial_facts.map((f) => [
                f.description || f.ref,
                fmtVal(typeof f.value === 'number' ? { value: f.value, unit: f.unit ?? null } : f.value),
              ])}
            />
          ) : (
            <T v="small" color={c.muted}>
              Nothing yet
            </T>
          )}
        </View>
        {spec.constraints.length ? (
          <>
            <T v="eyebrow" style={{ marginTop: 14 }}>
              Hard limits
            </T>
            {spec.constraints.map((k) => (
              <T key={k.id} v="small" style={{ marginTop: 4 }}>
                ⛨ {k.description || k.id} {k.hard ? '' : '(soft)'}
              </T>
            ))}
          </>
        ) : null}
      </Card>
      <Card style={{ flexGrow: 1, flexBasis: 300 }}>
        <T v="eyebrow">Scope</T>
        <View style={{ marginTop: 6 }}>
          <KV
            rows={[
              ['Time resolution', scope.time_resolution],
              ['Topics', scope.domain_labels.join(', ') || '—'],
              ['Jurisdiction', scope.jurisdiction || '—'],
              ['How the group decides', humanize(spec.governance_policy.kind)],
              ['Outside research', spec.research_policy.external_web],
            ]}
          />
        </View>
        {scope.notes.map((n) => (
          <T key={n} v="tiny" style={{ marginTop: 6 }}>
            {n}
          </T>
        ))}
      </Card>
    </View>
  );
}
