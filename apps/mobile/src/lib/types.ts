/* Response shapes of the UDSE v0.5 API (src/udse/api/app.py) as used by this client.
 * Only fields the app reads are typed; the server may send more. */

export type Tier = 'A_GROUNDED' | 'B_ASSUMPTION_BOUND' | 'C_ILLUSTRATIVE' | 'X_LIMITED_OR_ABSTAIN';
export type Quantity = { value: number; unit: string | null };
export type Scalar = string | number | boolean | null;
export type Value = Scalar | Quantity | Record<string, unknown> | unknown[];

export interface DevUser {
  user_id: string;
  display_name: string;
  token: string;
  fixture: string;
}
export interface DevUsers {
  dev_mode: boolean;
  warning: string;
  users: DevUser[];
}
export interface Me {
  user_id: string;
  display_name: string;
  profile_ids: string[];
}

export interface Horizon {
  starts_at: string;
  ends_at: string;
  clock_policy?: string;
  timezone?: string | null;
}

export interface DecisionSummary {
  decision_id: string;
  revision: number;
  question: string;
  participants: string[];
  role: 'owner' | 'participant' | string | null;
  model_version: number | null;
  promotion_state: string | null;
  runs: number;
  horizon: Horizon;
}

export interface Alternative {
  id: string;
  policy: string;
  label?: string | null;
  description?: string | null;
  suggested_by_system?: boolean;
  assumptions?: { ref?: string; description?: string; at?: string | null; value?: Value; delta?: Value; unit?: string | null }[];
}

export interface DecisionSpec {
  decision_id: string;
  revision: number;
  question: string;
  owner_id: string;
  participants: { agent_id: string; user_id?: string | null; display_name?: string | null; role?: string | null; profile_ref?: string | null }[];
  alternatives: Alternative[];
  horizon: Horizon;
  metrics: string[];
  initial_facts: { ref: string; value: Value; unit?: string | null; description?: string | null }[];
  constraints: { id: string; ref?: string | null; min?: number | null; max?: number | null; hard: boolean; description?: string | null }[];
  research_policy: { external_web: string };
  governance_policy: { kind: string };
  domain_labels?: string[];
  jurisdiction?: string | null;
}

export interface Scope {
  time_resolution: string;
  high_stakes: boolean;
  high_stakes_reasons: string[];
  alternatives: Alternative[];
  domain_labels: string[];
  jurisdiction: string | null;
  notes: string[];
}

export interface DecisionDetail {
  spec: DecisionSpec;
  scope: Scope;
  role: string | null;
}

export interface Scorecard {
  schema_validity?: string;
  mechanism_coverage?: string;
  empirical_support?: string;
  parameter_identifiability?: string;
  transfer_applicability?: string;
  trajectory_stability?: string;
  structural_omission_risk?: string;
  interpretive_limitations?: string[];
  [k: string]: unknown;
}
export interface Downgrade {
  id: string;
  from: Tier;
  to: Tier;
  reason: string;
}

export interface ModelSummary {
  model_id: string;
  version: number;
  hash: string;
  parent_version: number | null;
  revision_reason: string | null;
  promotion_state: string;
  created_at: string;
  passed: boolean;
  metric_fidelity: Record<string, Tier>;
  non_computable_metrics: Record<string, string>;
  scorecard: Scorecard;
  downgrades: Downgrade[];
  allowed_run_modes: string[];
  evidence_bundle_hash: string | null;
}

export interface Mechanism {
  id: string;
  operator: string;
  description: string;
  epistemic_tier: Tier;
  assumptions?: string[];
  evidence_refs?: string[];
  registry_ref?: { entry_id: string; version: string } | null;
}
export interface StructuralUnknown {
  id: string;
  description: string;
  impact: string;
  status: string;
  affects_metrics: string[];
}
export interface Omission {
  id: string;
  description: string;
  reason: string;
  risk: string;
}
export interface ReuseCheck {
  entry: string;
  kind: string;
  used: boolean;
  transfer_ok: boolean;
  reasons: string[];
  checks: Record<string, boolean>;
}

export interface ModelDetail extends ModelSummary {
  model: {
    purpose: string;
    mechanisms: Mechanism[];
    structural_unknowns: StructuralUnknown[];
    omissions: Omission[];
    reuse_manifest: ReuseCheck[];
  };
  registry_status: Record<string, string>;
}

export interface WorldModelNode {
  id: string;
  label: string;
  kind?: string;
  tier?: Tier | null;
  description?: string;
  counts?: Record<string, number>;
  actors?: string[];
  reuses?: string | null;
  assumptions?: string[];
}
export interface WorldModel {
  model: { version: number; hash: string; purpose: string; promotion_state: string; high_stakes: boolean };
  inputs: WorldModelNode[];
  processes: WorldModelNode[];
  missing: { id: string; label: string; description: string; impact: string; affects: string[]; status: string }[];
  outcomes: { id: string; label: string; tier: Tier; computable: boolean; reason: string; unit?: string | null; weakest: string[] }[];
  edges: { from: string; to: string; missing?: boolean }[];
  omissions: Omission[];
  versions: {
    version: number;
    reason: string | null;
    created_at: string;
    diff: null | { from_version: number; mechanisms_added: string[]; state_changed: unknown[]; banner: string };
  }[];
  scorecard: Scorecard;
  downgrades: Downgrade[];
}

export interface Claim {
  claim_id: string;
  origin_kind: string;
  record_type: string;
  subject: string;
  predicate: string;
  object: Value;
  unit: string | null;
  observed_at: string | null;
  expires_at: string | null;
  source_refs: { uri?: string | null }[];
  status: string;
  notes: string[];
}
export interface ResearchNeed {
  need_id: string;
  kind: string;
  question: string;
  status: string;
  priority: number;
  priority_rationale: string;
  consequence_if_unresolved: string;
}
export interface EvidenceBundle {
  as_of: string;
  claims: Claim[];
  needs: ResearchNeed[];
  rejected: { claim_id?: string; reason: string }[];
  conflicts: { subject: string; predicate: string; resolution: string; claims: string[] }[];
  warnings: string[];
}

export type CoverageStatus =
  'DEPTH_SIMULATED' | 'LOW_FIDELITY_EXPLORED' | 'REOPENED_AFTER_REVISION' | 'DISCOVERED_UNSIMULATED' | 'SET_ASIDE_BUDGET' | 'REJECTED_VALIDATED_CONSTRAINT';
export interface CoverageItem {
  id: string;
  label: string;
  description?: string;
  family_kind: string;
  status: CoverageStatus | string;
  status_reason: string;
  ui_label: string;
  citation: string[];
  run_ids: string[];
}
export interface Coverage {
  decision_id: string;
  model_version: number;
  items: CoverageItem[];
  discovery_methods: string[];
  not_yet_discovered_warning: string;
  stop_rationale: string | null;
  suspected_blind_spots: string[];
}

export type MomentKind = 'start' | 'end' | 'action' | 'agreement' | 'threshold' | 'external' | 'info' | 'blocked';
export interface Moment {
  i: number;
  seq: number | null;
  t: string;
  kind: MomentKind;
  headline: string;
  actor: string | null;
  tier: Tier | null;
  rationale: string | null;
  reasoning: string | null;
}
export interface Thread {
  thread_id: string;
  run_ids: string[];
  rep_run_id: string;
  label: string;
  detail: 'detailed' | 'preview' | 'probe';
  also: string[];
  family: string;
  outcome_label: string;
  status: string;
  ended_at: string;
  horizon_reached: boolean;
  stopped_early: boolean;
  carries: string[];
  weakest_tier: Tier | null;
  parent: string | null;
  split_index: number;
  split_at: string;
  shared_events?: number;
  first_difference: { self: string; parent: string } | null;
  series: Record<string, [string, number][]>;
  tier_path: [string, Tier][];
  moments: Moment[];
  synopsis: string;
}
export interface Ghost {
  id: string;
  label: string;
  status: string;
  ui_label: string;
  why: string;
  kind: 'unsimulated' | 'ruled_out' | 'set_aside' | 'reopened';
  citation: string[];
}
export interface Futures {
  decision_id: string;
  horizon: { start: string; end: string; resolution: string };
  model: { version: number; hash: string; high_stakes: boolean; versions: number[] };
  evidence_as_of: string;
  metrics: { id: string; label: string; unit: string | null; direction?: string }[];
  people: { id: string; name: string }[];
  families: { key: string; label: string; color_index: number }[];
  threads: Thread[];
  ghosts: Ghost[];
  ghosts_total: number;
  evidence: {
    verified: number;
    stale: number;
    unknown: number;
    contested: number;
    user: number;
    warnings: string[];
    gaps: { question: string; consequence: string; status: string; kind: string }[];
    as_of: string;
  };
  note: string;
}

export interface RunCard {
  run_id: string;
  name: string;
  policy_id?: string;
  scenario_ids?: string[];
  deadline_disclosure?: string;
  fidelity_mode?: 'screening' | 'depth' | 'sensitivity' | string;
  status: string;
  outcome?: string | null;
  reason?: string;
  synopsis?: string;
  horizon_reached?: boolean;
  weakest_tier?: Tier | null;
  caveats?: string[];
  events?: number;
  flags?: string[];
  model_version?: number;
}

export interface Change {
  ref: string;
  before: Value;
  after: Value;
}
export interface TimelineEvent {
  seq: number;
  event_id: string;
  sim_at: string;
  event_type: string;
  kind: string;
  actor: string | null;
  actor_name: string | null;
  summary: string;
  changes: Change[];
  tier: Tier | null;
  draws: { stream: string; value: unknown; distribution?: unknown }[];
  assumptions: string[];
  rationale: string | null;
  uncertainty: string | null;
  policy_kind: string | null;
  rejection_code: string | null;
  origin_kind: string;
}
export interface SeriesPoint {
  sim_at: string;
  seq: number;
  value: Value;
}
export interface Timeline {
  run_id: string;
  label: string;
  policy_id: string;
  scenario_ids: string[];
  deadline_disclosure: string;
  fidelity_mode: string;
  model_ref: { model_id: string; version: number; hash: string };
  status: string;
  terminal_outcome: string | null;
  reason: string | null;
  outcome_flags: string[];
  horizon_start: string;
  horizon_end: string;
  last_simulated_at: string;
  horizon_reached: boolean;
  grayed_after: string | null;
  grayed_reason: string | null;
  events: TimelineEvent[];
  series: Record<string, SeriesPoint[]>;
  metric_meta: Record<string, { label: string; unit: string | null; computable: boolean; reason: string }>;
  resolution_note: string;
}
export interface RunDetail {
  manifest: { run_id: string; decision_id: string; label: string };
  state: string;
  partial_horizon_end: string | null;
  card: RunCard | null;
}
export interface StateAt {
  run_id: string;
  seq: number;
  sim_at: string;
  world: Record<string, { value: Value; unit: string | null; tier: Tier | null; label: string; visibility: string; origin: string }>;
}
export interface WhyStep {
  seq: number;
  sim_at: string;
  category: string;
  text: string;
  headline?: string;
  rationale?: string | null;
  reasoning?: string | null;
  mechanisms?: { id: string; description: string; tier: Tier }[];
  random_draws?: { stream: string; value: unknown }[];
}
export interface Why {
  run_id: string;
  seq: number;
  chain: WhyStep[];
  limitations: string[];
}
export interface PromotionCheck {
  event_id: string;
  origin_kind: string;
  attempts: { path: string; refused: boolean; detail: string }[];
  all_refused: boolean;
  layer_a_hash_unchanged: boolean;
}
export interface Compare {
  run_a: string;
  run_b: string;
  same_model: boolean;
  banner: string | null;
  intervention: Record<string, { a: unknown; b: unknown }>;
  common_prefix_events: number;
  common_ancestor_state_hash: string | null;
  first_divergence: { a: { seq: number; sim_at: string; text: string } | null; b: { seq: number; sim_at: string; text: string } | null };
  outcomes: { a: string | null; b: string | null; a_status: string; b_status: string };
  metrics: Record<string, { a: { status: string; value?: Value } | null; b: { status: string; value?: Value } | null }>;
  counterfactual?: string | null;
  limitations: string[];
}

export interface ProfileFeature {
  key: string;
  value: Value;
  status: 'CONFIRMED' | 'INFERRED' | 'UNKNOWN' | 'HARD_CONSTRAINT' | string;
  evidence_refs: string[];
  note?: string;
}
export interface Proposal {
  proposal_id: string;
  status: 'PROPOSED' | 'APPROVED' | 'REJECTED' | string;
  mode: 'shadow' | 'visible' | string;
  reason: string;
  evidence_ids: string[];
  changes: { key: string; change_kind?: string; before: { value: Value } | null; after: { value: Value; status?: string } | null }[];
}
export interface Profile {
  current: { profile_id: string; version: number; features: ProfileFeature[] };
  versions: { version: number; created_at: string; reason: string }[];
  proposals: Proposal[];
  evidence: { event_id: string; payload: Record<string, unknown> }[];
}

export interface AuditRow {
  id: number;
  at: string;
  principal: string;
  role: string;
  operation: string;
  target: string;
  allowed: number;
  reason: string;
}

export interface Job {
  job_id: string;
  status: 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | string;
  result: unknown;
  error: string | null;
}
export interface JobRef {
  job_id: string;
  created: boolean;
  status_url: string;
}
