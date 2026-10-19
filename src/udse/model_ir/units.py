"""Minimal dimensional-analysis unit system for the Model IR (§4.1 "typed state variables with units").

A unit is a product of named dimensions with integer exponents plus a scale factor to the
dimension's base unit. Unknown unit tokens (``person``, ``customer``) become their own count
dimensions, so adding customers to people is a compile error rather than a silent bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..common import SECONDS_PER_MONTH

_KNOWN: dict[str, tuple[dict[str, int], float]] = {
    "1": ({}, 1.0),
    "ratio": ({}, 1.0),
    "fraction": ({}, 1.0),
    "percent": ({}, 0.01),
    "%": ({}, 0.01),
    "USD": ({"USD": 1}, 1.0),
    "EUR": ({"EUR": 1}, 1.0),
    "second": ({"time": 1}, 1.0),
    "minute": ({"time": 1}, 60.0),
    "hour": ({"time": 1}, 3600.0),
    "day": ({"time": 1}, 86400.0),
    "week": ({"time": 1}, 604800.0),
    "month": ({"time": 1}, SECONDS_PER_MONTH),
    "year": ({"time": 1}, 12 * SECONDS_PER_MONTH),
}

_ALIASES = {
    "s": "second", "sec": "second", "seconds": "second",
    "min": "minute", "minutes": "minute",
    "h": "hour", "hr": "hour", "hours": "hour",
    "days": "day", "weeks": "week", "months": "month", "years": "year", "yr": "year",
    "people": "person", "persons": "person", "dimensionless": "1", "": "1",
}

TIME_UNITS = ("second", "minute", "hour", "day", "week", "month", "year")


class UnitError(ValueError):
    pass


@dataclass(frozen=True)
class Unit:
    dims: tuple[tuple[str, int], ...]
    scale: float
    label: str

    @property
    def is_dimensionless(self) -> bool:
        return not self.dims

    def same_dimension(self, other: "Unit") -> bool:
        return self.dims == other.dims

    def factor_to(self, other: "Unit") -> float:
        if not self.same_dimension(other):
            raise UnitError(f"incompatible units: {self.label} vs {other.label}")
        return self.scale / other.scale

    def __mul__(self, other: "Unit") -> "Unit":
        return _combine(self, other, 1)

    def __truediv__(self, other: "Unit") -> "Unit":
        return _combine(self, other, -1)

    def __str__(self) -> str:
        return self.label


def canonical_label(dims: tuple[tuple[str, int], ...], scale: float) -> str:
    """Readable label for a derived unit: a known unit name when one matches exactly, else a
    product of base units (time in seconds) with an explicit scale factor when needed."""
    for name, (kd, ks) in _KNOWN.items():
        if tuple(sorted(kd.items())) == dims and abs(ks - scale) <= 1e-12 * max(1.0, abs(scale)) and name not in (
            "ratio", "fraction", "%"
        ):
            return name
    base = {"time": "second"}
    num = [f"{base.get(k, k)}" + (f"^{e}" if e != 1 else "") for k, e in dims if e > 0]
    den = [f"{base.get(k, k)}" + (f"^{-e}" if e != -1 else "") for k, e in dims if e < 0]
    label = "*".join(num) or "1"
    if den:
        label += "/" + "/".join(den)
    if abs(scale - 1.0) > 1e-12:
        label = f"{scale:g}x {label}"
    return label


def _combine(a: Unit, b: Unit, sign: int) -> Unit:
    d = dict(a.dims)
    for k, e in b.dims:
        d[k] = d.get(k, 0) + sign * e
    dims = tuple(sorted((k, e) for k, e in d.items() if e != 0))
    scale = a.scale * (b.scale if sign > 0 else 1.0 / b.scale)
    if a.is_dimensionless and a.scale == 1.0 and sign > 0:
        label = b.label
    elif b.is_dimensionless and b.scale == 1.0:
        label = a.label
    else:
        label = canonical_label(dims, scale)
    return Unit(dims, scale, label)


def base_unit(dims: tuple[tuple[str, int], ...]) -> Unit:
    return Unit(tuple(dims), 1.0, canonical_label(tuple(dims), 1.0))


def _token(tok: str) -> Unit:
    raw = tok.strip()
    exp = 1
    if "^" in raw:
        raw, e = raw.split("^", 1)
        try:
            exp = int(e)
        except ValueError as ex:
            raise UnitError(f"bad exponent in unit token {tok!r}") from ex
    name = _ALIASES.get(raw, raw)
    if name in _KNOWN:
        dims, scale = _KNOWN[name]
    else:
        if not name.replace("_", "").isalnum():
            raise UnitError(f"invalid unit token {tok!r}")
        dims, scale = {name: 1}, 1.0
    out = Unit(tuple(sorted(dims.items())), scale, name)
    result = DIMENSIONLESS
    for _ in range(abs(exp)):
        result = result * out if exp > 0 else result / out
    return result


@lru_cache(maxsize=1024)
def parse_unit(text: str | None) -> Unit:
    """Parse ``"USD/month"``, ``"person*hour"``, ``"USD/person/month"`` (all after the first ``/``
    are denominators)."""
    if text is None:
        return DIMENSIONLESS
    s = text.strip()
    if s in ("", "1"):
        return DIMENSIONLESS
    parts = s.split("/")
    result = DIMENSIONLESS
    for i, part in enumerate(parts):
        for tok in part.split("*"):
            if not tok.strip():
                raise UnitError(f"empty unit token in {text!r}")
            u = _token(tok)
            result = result * u if i == 0 else result / u
    return Unit(result.dims, result.scale, s)


DIMENSIONLESS = Unit((), 1.0, "1")


def convert(value: float, src: Unit | str, dst: Unit | str) -> float:
    s = parse_unit(src) if isinstance(src, str) else src
    d = parse_unit(dst) if isinstance(dst, str) else dst
    return value * s.factor_to(d)
