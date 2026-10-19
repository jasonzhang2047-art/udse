"""Shared primitives: canonical hashing, identifiers, simulated-time arithmetic and errors.

Everything that is hashed (model IR, state snapshots, events) goes through ``canonical_json`` so
that event-log replay can reconstruct states bit-for-bit (spec §8.4).
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


class UDSEError(Exception):
    """Base error for the engine."""


class PermissionDenied(UDSEError):
    """A principal attempted a write outside its authority (epistemic isolation, §3.3)."""


class ProvenanceRejected(UDSEError):
    """Evidence or profile ingestion refused because of origin/lineage (§3.1 non-negotiable)."""


class ModelExecutionError(UDSEError):
    """A transition could not be executed under the pinned model (→ MODEL_EXECUTION_BLOCKED)."""


# ---------------------------------------------------------------------------
# Canonical JSON + hashing
# ---------------------------------------------------------------------------

def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=_json_default
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: Any) -> str:
    return "sha256:" + sha256_hex(canonical_json(obj))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def json_copy(obj: Any) -> Any:
    """Deep copy restricted to JSON values (fast and guarantees serialisability)."""
    return json.loads(canonical_json(obj))


# ---------------------------------------------------------------------------
# Simulated time
# ---------------------------------------------------------------------------

def parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"simulated timestamps must carry a timezone offset: {value!r}")
    return dt


def iso(dt: datetime) -> str:
    return dt.isoformat()


def add_months(dt: datetime, months: int) -> datetime:
    y, m = divmod(dt.month - 1 + months, 12)
    year, month = dt.year + y, m + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


_DURATION_RE = re.compile(
    r"^(?P<sign>-)?P(?:(?P<y>\d+(?:\.\d+)?)Y)?(?:(?P<mo>\d+(?:\.\d+)?)M)?(?:(?P<w>\d+(?:\.\d+)?)W)?"
    r"(?:(?P<d>\d+(?:\.\d+)?)D)?(?:T(?:(?P<h>\d+(?:\.\d+)?)H)?(?:(?P<mi>\d+(?:\.\d+)?)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?)?$"
)

SECONDS_PER_MONTH = 30.436875 * 86400.0  # mean Gregorian month, used only for unit conversion


@dataclass(frozen=True)
class Duration:
    """ISO-8601 duration split into calendar months and exact seconds."""

    months: int = 0
    seconds: float = 0.0

    @classmethod
    def parse(cls, text: str) -> "Duration":
        m = _DURATION_RE.match(text.strip())
        if not m or text.strip() in ("P", "PT", "-P"):
            raise ValueError(f"invalid ISO-8601 duration: {text!r}")
        g = {k: float(v) if v else 0.0 for k, v in m.groupdict().items() if k != "sign"}
        months = g["y"] * 12 + g["mo"]
        if months != int(months):
            raise ValueError(f"fractional months/years are not supported: {text!r}")
        seconds = g["w"] * 604800 + g["d"] * 86400 + g["h"] * 3600 + g["mi"] * 60 + g["s"]
        sign = -1 if m.group("sign") else 1
        return cls(months=sign * int(months), seconds=sign * seconds)

    def add_to(self, dt: datetime) -> datetime:
        out = add_months(dt, self.months) if self.months else dt
        return out + timedelta(seconds=self.seconds) if self.seconds else out

    @property
    def is_negative(self) -> bool:
        return self.months < 0 or self.seconds < 0

    @property
    def is_zero(self) -> bool:
        return self.months == 0 and self.seconds == 0

    def approx_seconds(self) -> float:
        return self.months * SECONDS_PER_MONTH + self.seconds


def resolve_time(spec: str, base: datetime) -> datetime:
    """Resolve an absolute ISO timestamp or an offset ("+PT30M" / "P1M") from ``base``."""
    s = spec.strip()
    if s.startswith("+"):
        return Duration.parse(s[1:]).add_to(base)
    if s.startswith("P") or s.startswith("-P"):
        return Duration.parse(s).add_to(base)
    return parse_dt(s)
