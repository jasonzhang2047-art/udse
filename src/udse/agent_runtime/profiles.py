"""Profile snapshots, sparse working sets and run-local overlays (§7.1, §7.2).

RealProfileVersion is descriptive and only changes through the human-evidence workflow.
RunAgentConfig overlays are declared counterfactual hypotheses applied to a *copy* for one run.
"""

from __future__ import annotations

from ..model_ir.enums import FeatureStatus
from ..model_ir.schemas import FeatureOverlay, ProfileFeature, RealProfileVersion

WORKING_SET_MIN, WORKING_SET_MAX = 5, 12


def effective_features(features: list[ProfileFeature], overlays: list[FeatureOverlay]) -> list[ProfileFeature]:
    by_key = {f.key: f.model_copy(deep=True) for f in features}
    for o in overlays:
        base = by_key.get(o.key)
        status = base.status if base and base.status == FeatureStatus.HARD_CONSTRAINT else FeatureStatus.INFERRED
        by_key[o.key] = ProfileFeature(
            key=o.key, value=o.value, status=status, scope="run_overlay", epistemic_confidence="unknown",
            note=f"RUN OVERLAY (hypothesis, not real profile): {o.hypothesis}",
            privacy=base.privacy if base else "private",
        )
    return list(by_key.values())


def select_working_set(profile: RealProfileVersion, decision_dims: set[str], limit: int = WORKING_SET_MAX) -> list[ProfileFeature]:
    """Sparse active working set: features whose dimension matters to this decision first, then by
    stated relevance. Hard constraints on relevant dimensions are always kept."""

    def dim(f: ProfileFeature) -> str:
        return f.key.split(".", 1)[1] if "." in f.key else f.key

    relevant = [f for f in profile.features if dim(f) in decision_dims or f.key.startswith("behavior.")]
    hard = [f for f in relevant if f.status == FeatureStatus.HARD_CONSTRAINT]
    soft = sorted(
        (f for f in relevant if f.status != FeatureStatus.HARD_CONSTRAINT),
        key=lambda f: (-(f.relevance or 0.0), f.key),
    )
    chosen = hard + soft[: max(0, limit - len(hard))]
    return chosen


def snapshot_ref(profile: RealProfileVersion) -> str:
    return f"{profile.profile_id}@v{profile.version}"
