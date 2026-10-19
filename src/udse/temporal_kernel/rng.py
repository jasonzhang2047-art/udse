"""Counter-based random streams (§8.4 common random numbers).

Each draw is a pure function of (stream seed, stream name, mechanism id, simulated time,
occurrence). Paired arms therefore see aligned exogenous variates at the same simulated moments
even after their action-driven event sequences diverge, while each draw is still recorded on the
committed event so transition replay never needs to re-derive it.
"""

from __future__ import annotations

import hashlib
from statistics import NormalDist
from typing import Any

from ..model_ir.schemas import Distribution

DEFAULT_STREAM_SEED = 20261018


class CounterRNG:
    def __init__(self, seeds: dict[str, int]):
        self.seeds = dict(seeds)

    def uniform(self, stream: str, key: str) -> float:
        seed = self.seeds.get(stream, self.seeds.get("default", DEFAULT_STREAM_SEED))
        h = hashlib.sha256(f"{seed}|{stream}|{key}".encode()).digest()
        return (int.from_bytes(h[:8], "big") + 0.5) / 2.0**64

    def sample(self, stream: str, key: str, dist: Distribution) -> tuple[Any, float]:
        u = self.uniform(stream, key)
        k = dist.kind
        if k == "bernoulli":
            if dist.p is None or not 0.0 <= dist.p <= 1.0:
                raise ValueError("bernoulli requires 0 <= p <= 1")
            return (u < dist.p), u
        if k == "uniform":
            return dist.low + u * (dist.high - dist.low), u
        if k == "triangular":
            a, b, c = dist.low, dist.high, dist.mode
            fc = (c - a) / (b - a) if b > a else 0.5
            if u < fc:
                return a + ((b - a) * (c - a) * u) ** 0.5, u
            return b - ((b - a) * (b - c) * (1 - u)) ** 0.5, u
        if k == "normal":
            v = NormalDist(dist.mean, dist.sd).inv_cdf(min(max(u, 1e-12), 1 - 1e-12))
            if dist.low is not None:
                v = max(v, dist.low)
            if dist.high is not None:
                v = min(v, dist.high)
            return v, u
        if k == "choice":
            opts = dist.options or []
            total = sum(float(o.get("weight", 1.0)) for o in opts)
            acc = 0.0
            for o in opts:
                acc += float(o.get("weight", 1.0)) / total
                if u <= acc:
                    return o["value"], u
            return opts[-1]["value"], u
        raise ValueError(f"unsupported distribution {k}")
