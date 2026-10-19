"""Safe declarative expression language for the Model IR.

LLM-proposed models may only contain expressions in this language (§6.1: "code execution and
authoritative world mutation remain restricted to approved typed operators"). There is no
``eval``: a recursive-descent parser builds an AST that is interpreted with

* unit-carrying quantities (``5000[USD]``, ``company.cash_usd / company.monthly_outflow_usd``),
* Kleene three-valued logic where ``UNKNOWN`` is a first-class value (§5.3 "UNKNOWN is a valid
  field value") — arithmetic or comparison with UNKNOWN yields UNKNOWN rather than a guess,
* a static type/unit inference pass used by the compiler.

Grammar::

    expr    := or
    or      := and ("or" and)*
    and     := not ("and" not)*
    not     := "not" not | cmp
    cmp     := sum (("<"|"<="|">"|">="|"=="|"!=") sum)?
    sum     := term (("+"|"-") term)*
    term    := unary (("*"|"/") unary)*
    unary   := "-" unary | primary
    primary := NUMBER ["[" UNIT "]"] | STRING | true | false | UNKNOWN
             | NAME ["(" args ")"] | "(" expr ")"
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Callable, Protocol

from ..common import parse_dt
from .units import DIMENSIONLESS, Unit, UnitError, parse_unit

MAX_EXPR_LEN = 2000
MAX_DEPTH = 40


class ExprError(ValueError):
    """Syntax / static type error in an expression."""


class EvalError(RuntimeError):
    """Runtime evaluation error (e.g., division by zero, missing reference)."""


class _Unknown:
    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:  # pragma: no cover - guard against accidental truthiness
        raise EvalError("UNKNOWN has no truth value; use three-valued operators")


UNKNOWN = _Unknown()
UNKNOWN_JSON = "UNKNOWN"


def is_unknown(v: Any) -> bool:
    return v is UNKNOWN


@dataclass(frozen=True)
class Q:
    """A quantity: numeric value with a unit."""

    value: float
    unit: Unit

    def to(self, unit: Unit) -> "Q":
        return Q(self.value * self.unit.factor_to(unit), unit)

    def __repr__(self) -> str:
        return f"{self.value:g}[{self.unit.label}]"


# ---------------------------------------------------------------------------
# Tokenizer + parser
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<unit>\[[^\[\]]*\])
  | (?P<str>'[^']*'|"[^"]*")
  | (?P<op><=|>=|==|!=|<|>|\+|-|\*|/|\(|\)|,)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)
    """,
    re.VERBOSE,
)

KEYWORDS = {"and", "or", "not", "true", "false", "UNKNOWN"}
FUNCTIONS = {
    "min", "max", "abs", "clamp", "if", "is_known", "coalesce", "field", "time", "to",
    "floor", "ceil", "round", "count",
}


def _tokenize(text: str) -> list[tuple[str, str]]:
    if len(text) > MAX_EXPR_LEN:
        raise ExprError("expression too long")
    pos, out = 0, []
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise ExprError(f"unexpected character {text[pos]!r} at {pos} in {text!r}")
        kind = m.lastgroup
        val = m.group(kind)
        pos = m.end()
        if kind == "ws":
            continue
        out.append((kind, val))
    out.append(("eof", ""))
    return out


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0
        self.depth = 0

    def peek(self) -> tuple[str, str]:
        return self.toks[self.i]

    def take(self) -> tuple[str, str]:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind: str, val: str | None = None) -> tuple[str, str]:
        t = self.take()
        if t[0] != kind or (val is not None and t[1] != val):
            raise ExprError(f"expected {val or kind}, got {t[1]!r} in {self.text!r}")
        return t

    def _enter(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ExprError("expression nesting too deep")

    def parse(self):
        node = self.p_or()
        if self.peek()[0] != "eof":
            raise ExprError(f"unexpected token {self.peek()[1]!r} in {self.text!r}")
        return node

    def p_or(self):
        node = self.p_and()
        while self.peek() == ("name", "or"):
            self.take()
            node = ("bin", "or", node, self.p_and())
        return node

    def p_and(self):
        node = self.p_not()
        while self.peek() == ("name", "and"):
            self.take()
            node = ("bin", "and", node, self.p_not())
        return node

    def p_not(self):
        if self.peek() == ("name", "not"):
            self.take()
            self._enter()
            node = ("un", "not", self.p_not())
            self.depth -= 1
            return node
        return self.p_cmp()

    def p_cmp(self):
        node = self.p_sum()
        if self.peek()[0] == "op" and self.peek()[1] in ("<", "<=", ">", ">=", "==", "!="):
            op = self.take()[1]
            node = ("bin", op, node, self.p_sum())
        return node

    def p_sum(self):
        node = self.p_term()
        while self.peek()[0] == "op" and self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            node = ("bin", op, node, self.p_term())
        return node

    def p_term(self):
        node = self.p_unary()
        while self.peek()[0] == "op" and self.peek()[1] in ("*", "/"):
            op = self.take()[1]
            node = ("bin", op, node, self.p_unary())
        return node

    def p_unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            self._enter()
            node = ("un", "neg", self.p_unary())
            self.depth -= 1
            return node
        return self.p_primary()

    def p_primary(self):
        kind, val = self.take()
        if kind == "num":
            unit = None
            if self.peek()[0] == "unit":
                unit = self.take()[1][1:-1].strip()
                try:
                    parse_unit(unit)
                except UnitError as e:
                    raise ExprError(str(e)) from e
            return ("num", float(val), unit)
        if kind == "str":
            return ("str", val[1:-1])
        if kind == "op" and val == "(":
            self._enter()
            node = self.p_or()
            self.depth -= 1
            self.expect("op", ")")
            return node
        if kind == "name":
            if val == "true":
                return ("bool", True)
            if val == "false":
                return ("bool", False)
            if val == "UNKNOWN":
                return ("unknown",)
            if val in ("and", "or", "not"):
                raise ExprError(f"unexpected keyword {val!r} in {self.text!r}")
            if self.peek() == ("op", "("):
                if val not in FUNCTIONS:
                    raise ExprError(f"unknown function {val!r} (allowed: {sorted(FUNCTIONS)})")
                self.take()
                args = []
                if self.peek() != ("op", ")"):
                    self._enter()
                    args.append(self.p_or())
                    while self.peek() == ("op", ","):
                        self.take()
                        args.append(self.p_or())
                    self.depth -= 1
                self.expect("op", ")")
                return ("call", val, tuple(args))
            return ("name", val)
        raise ExprError(f"unexpected token {val!r} in {self.text!r}")


@lru_cache(maxsize=4096)
def parse_expr(text: str):
    if not isinstance(text, str):
        raise ExprError(f"expression must be a string, got {type(text).__name__}")
    return _Parser(text).parse()


def referenced_names(text: str) -> set[str]:
    """All bare names referenced (state refs, ``param.*``, ``arg.*``, ``event.*``...)."""
    out: set[str] = set()

    def walk(n):
        tag = n[0]
        if tag == "name":
            out.add(n[1])
        elif tag == "un":
            walk(n[2])
        elif tag == "bin":
            walk(n[2])
            walk(n[3])
        elif tag == "call":
            for a in n[2]:
                walk(a)

    walk(parse_expr(text))
    return out


def field_refs(text: str) -> list[tuple[Any, str]]:
    """(entity_node, var) pairs used through ``field(entity, 'var')``."""
    out = []

    def walk(n):
        tag = n[0]
        if tag == "call":
            if n[1] == "field" and len(n[2]) == 2 and n[2][1][0] == "str":
                out.append((n[2][0], n[2][1][1]))
            for a in n[2]:
                walk(a)
        elif tag == "un":
            walk(n[2])
        elif tag == "bin":
            walk(n[2])
            walk(n[3])

    walk(parse_expr(text))
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

class Env(Protocol):
    def lookup(self, name: str) -> Any: ...


class DictEnv:
    def __init__(self, values: dict[str, Any], parent: Env | None = None):
        self.values = values
        self.parent = parent

    def lookup(self, name: str) -> Any:
        if name in self.values:
            return self.values[name]
        if self.parent is not None:
            return self.parent.lookup(name)
        raise EvalError(f"unresolved reference {name!r}")


def _is_zero_literal(q: Any) -> bool:
    return isinstance(q, Q) and q.unit.is_dimensionless and q.value == 0.0


def _num(v: Any, what: str) -> Q:
    if not isinstance(v, Q):
        raise EvalError(f"{what}: expected quantity, got {type(v).__name__}")
    return v


def _addsub(op: str, a: Any, b: Any) -> Any:
    if isinstance(a, datetime) and isinstance(b, datetime):
        if op != "-":
            raise EvalError("timestamp + timestamp is undefined")
        return Q((a - b).total_seconds(), parse_unit("second"))
    if isinstance(a, datetime):
        b = _num(b, op)
        if not b.unit.same_dimension(parse_unit("second")):
            raise EvalError("timestamp arithmetic requires a time quantity")
        secs = b.to(parse_unit("second")).value
        return a + timedelta(seconds=secs if op == "+" else -secs)
    if isinstance(b, datetime):
        if op == "-":
            raise EvalError("quantity - timestamp is undefined")
        return _addsub("+", b, a)
    a, b = _num(a, op), _num(b, op)
    if _is_zero_literal(b):
        return a
    if _is_zero_literal(a):
        return b if op == "+" else Q(-b.value, b.unit)
    try:
        bv = b.to(a.unit).value
    except UnitError as e:
        raise EvalError(str(e)) from e
    return Q(a.value + bv if op == "+" else a.value - bv, a.unit)


def add_sub(op: str, a: Any, b: Any) -> Any:
    """Public unit-aware ``a + b`` / ``a - b`` with UNKNOWN propagation."""
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return _addsub(op, a, b)


def compare(op: str, a: Any, b: Any) -> Any:
    """Public unit-aware comparison with UNKNOWN propagation."""
    if a is UNKNOWN or b is UNKNOWN:
        return UNKNOWN
    return _cmp(op, a, b)


def _cmp(op: str, a: Any, b: Any) -> bool:
    if isinstance(a, datetime) and isinstance(b, datetime):
        x, y = a, b
    elif isinstance(a, Q) and isinstance(b, Q):
        if _is_zero_literal(b):
            x, y = a.value, 0.0
        elif _is_zero_literal(a):
            x, y = 0.0, b.value
        else:
            try:
                x, y = a.value, b.to(a.unit).value
            except UnitError as e:
                raise EvalError(str(e)) from e
    elif isinstance(a, (str, bool)) and type(a) is type(b):
        if op not in ("==", "!="):
            raise EvalError(f"ordering comparison on {type(a).__name__}")
        x, y = a, b
    else:
        raise EvalError(f"cannot compare {type(a).__name__} with {type(b).__name__}")
    return {
        "<": lambda: x < y, "<=": lambda: x <= y, ">": lambda: x > y,
        ">=": lambda: x >= y, "==": lambda: x == y, "!=": lambda: x != y,
    }[op]()


def _truth(v: Any) -> Any:
    if v is UNKNOWN or isinstance(v, bool):
        return v
    raise EvalError(f"expected boolean, got {type(v).__name__}")


def _values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, Q) and isinstance(b, Q):
        try:
            return a.value == b.to(a.unit).value
        except UnitError:
            return False
    return type(a) is type(b) and a == b


def evaluate(text_or_node: Any, env: Env) -> Any:
    node = parse_expr(text_or_node) if isinstance(text_or_node, str) else text_or_node
    return _eval(node, env)


def _eval(n, env: Env) -> Any:
    tag = n[0]
    if tag == "num":
        return Q(n[1], parse_unit(n[2]) if n[2] else DIMENSIONLESS)
    if tag == "str":
        return n[1]
    if tag == "bool":
        return n[1]
    if tag == "unknown":
        return UNKNOWN
    if tag == "name":
        return env.lookup(n[1])
    if tag == "un":
        v = _eval(n[2], env)
        if n[1] == "not":
            v = _truth(v)
            return UNKNOWN if v is UNKNOWN else (not v)
        if v is UNKNOWN:
            return UNKNOWN
        v = _num(v, "negation")
        return Q(-v.value, v.unit)
    if tag == "bin":
        op = n[1]
        if op == "and":
            a = _truth(_eval(n[2], env))
            if a is False:
                return False
            b = _truth(_eval(n[3], env))
            if b is False:
                return False
            return UNKNOWN if (a is UNKNOWN or b is UNKNOWN) else True
        if op == "or":
            a = _truth(_eval(n[2], env))
            if a is True:
                return True
            b = _truth(_eval(n[3], env))
            if b is True:
                return True
            return UNKNOWN if (a is UNKNOWN or b is UNKNOWN) else False
        a, b = _eval(n[2], env), _eval(n[3], env)
        if a is UNKNOWN or b is UNKNOWN:
            return UNKNOWN
        if op in ("+", "-"):
            return _addsub(op, a, b)
        if op in ("*", "/"):
            a, b = _num(a, op), _num(b, op)
            if op == "*":
                return Q(a.value * b.value, a.unit * b.unit)
            if b.value == 0:
                raise EvalError("division by zero")
            return Q(a.value / b.value, a.unit / b.unit)
        return _cmp(op, a, b)
    if tag == "call":
        return _call(n[1], n[2], env)
    raise EvalError(f"bad node {n!r}")


def _call(fn: str, args, env: Env) -> Any:
    def ev(i):
        return _eval(args[i], env)

    def arity(k):
        if len(args) != k:
            raise EvalError(f"{fn}() takes {k} arguments")

    if fn == "if":
        arity(3)
        c = _truth(ev(0))
        if c is True:
            return ev(1)
        if c is False:
            return ev(2)
        a, b = ev(1), ev(2)
        return a if _values_equal(a, b) else UNKNOWN
    if fn == "is_known":
        arity(1)
        return ev(0) is not UNKNOWN
    if fn == "coalesce":
        arity(2)
        v = ev(0)
        return ev(1) if v is UNKNOWN else v
    if fn == "field":
        arity(2)
        ent = ev(0)
        if ent is UNKNOWN:
            return UNKNOWN
        if not isinstance(ent, str) or args[1][0] != "str":
            raise EvalError("field(entity, 'var') requires a string entity and literal var name")
        return env.lookup(f"{ent}.{args[1][1]}")
    if fn == "time":
        arity(1)
        if args[0][0] != "str":
            raise EvalError("time() requires an ISO string literal")
        return parse_dt(args[0][1])
    if fn == "to":
        arity(2)
        v = ev(0)
        if v is UNKNOWN:
            return UNKNOWN
        if args[1][0] != "str":
            raise EvalError("to(x, 'unit') requires a literal unit")
        try:
            return _num(v, "to").to(parse_unit(args[1][1]))
        except UnitError as e:
            raise EvalError(str(e)) from e
    if fn == "count":
        vals = [_truth(_eval(a, env)) for a in args]
        if any(v is UNKNOWN for v in vals):
            return UNKNOWN
        return Q(float(sum(1 for v in vals if v)), DIMENSIONLESS)
    vals = [_eval(a, env) for a in args]
    if any(v is UNKNOWN for v in vals):
        return UNKNOWN
    if fn in ("min", "max"):
        if not vals:
            raise EvalError(f"{fn}() needs arguments")
        qs = [_num(v, fn) for v in vals]
        unit = qs[0].unit
        try:
            conv = [q.to(unit).value for q in qs]
        except UnitError as e:
            raise EvalError(str(e)) from e
        return Q(min(conv) if fn == "min" else max(conv), unit)
    if fn == "abs":
        arity(1)
        q = _num(vals[0], fn)
        return Q(abs(q.value), q.unit)
    if fn in ("floor", "ceil", "round"):
        arity(1)
        q = _num(vals[0], fn)
        f: Callable[[float], float] = {"floor": math.floor, "ceil": math.ceil, "round": round}[fn]
        return Q(float(f(q.value)), q.unit)
    if fn == "clamp":
        arity(3)
        x, lo, hi = (_num(v, fn) for v in vals)
        try:
            lo_v, hi_v = lo.to(x.unit).value, hi.to(x.unit).value
        except UnitError as e:
            raise EvalError(str(e)) from e
        return Q(min(max(x.value, lo_v), hi_v), x.unit)
    raise EvalError(f"unknown function {fn}")


# ---------------------------------------------------------------------------
# Static type / unit inference
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class T:
    kind: str  # quantity | bool | str | time | any
    unit: Unit | None = None
    values: frozenset[str] | None = None
    zero_literal: bool = False

    def describe(self) -> str:
        if self.kind == "quantity":
            return f"quantity[{self.unit.label if self.unit else '?'}]"
        if self.kind == "str" and self.values:
            return f"enum{sorted(self.values)}"
        return self.kind


T_BOOL = T("bool")
T_ANY = T("any")
T_TIME = T("time")
T_SECONDS = T("quantity", parse_unit("second"))


def T_STR(values=None) -> T:
    return T("str", values=frozenset(values) if values else None)


def T_Q(unit: Unit | str | None) -> T:
    return T("quantity", parse_unit(unit) if not isinstance(unit, Unit) else unit)


class TypeEnv(Protocol):
    def lookup_type(self, name: str) -> T: ...


class DictTypeEnv:
    def __init__(self, types: dict[str, T], parent: TypeEnv | None = None):
        self.types = types
        self.parent = parent

    def lookup_type(self, name: str) -> T:
        if name in self.types:
            return self.types[name]
        if self.parent is not None:
            return self.parent.lookup_type(name)
        raise ExprError(f"unresolved reference {name!r}")


def infer(text_or_node: Any, tenv: TypeEnv) -> T:
    node = parse_expr(text_or_node) if isinstance(text_or_node, str) else text_or_node
    return _infer(node, tenv)


def _need_bool(t: T, ctx: str):
    if t.kind not in ("bool", "any"):
        raise ExprError(f"{ctx}: expected boolean, got {t.describe()}")


def _compat_q(a: T, b: T, ctx: str):
    if a.kind == "any" or b.kind == "any":
        return
    if a.kind != "quantity" or b.kind != "quantity":
        raise ExprError(f"{ctx}: expected quantities, got {a.describe()} and {b.describe()}")
    if a.zero_literal or b.zero_literal:
        return
    if not a.unit.same_dimension(b.unit):
        raise ExprError(f"{ctx}: unit mismatch {a.unit.label} vs {b.unit.label}")


def _infer(n, tenv: TypeEnv) -> T:
    tag = n[0]
    if tag == "num":
        unit = parse_unit(n[2]) if n[2] else DIMENSIONLESS
        return T("quantity", unit, zero_literal=(n[1] == 0.0 and not n[2]))
    if tag == "str":
        return T_STR([n[1]])
    if tag == "bool":
        return T_BOOL
    if tag == "unknown":
        return T_ANY
    if tag == "name":
        return tenv.lookup_type(n[1])
    if tag == "un":
        t = _infer(n[2], tenv)
        if n[1] == "not":
            _need_bool(t, "not")
            return T_BOOL
        if t.kind not in ("quantity", "any"):
            raise ExprError(f"negation of {t.describe()}")
        return t
    if tag == "bin":
        op = n[1]
        a, b = _infer(n[2], tenv), _infer(n[3], tenv)
        if op in ("and", "or"):
            _need_bool(a, op)
            _need_bool(b, op)
            return T_BOOL
        if op in ("+", "-"):
            if a.kind == "time":
                if b.kind == "time" and op == "-":
                    return T_SECONDS
                _compat_q(T_SECONDS, b, f"timestamp {op}")
                return T_TIME
            if b.kind == "time":
                if op == "-":
                    raise ExprError("quantity - timestamp is undefined")
                _compat_q(T_SECONDS, a, "timestamp +")
                return T_TIME
            _compat_q(a, b, op)
            if a.kind == "any":
                return b
            return a if not a.zero_literal else b
        if op in ("*", "/"):
            if a.kind == "any" or b.kind == "any":
                return T_ANY
            if a.kind != "quantity" or b.kind != "quantity":
                raise ExprError(f"{op}: expected quantities, got {a.describe()} and {b.describe()}")
            return T("quantity", a.unit * b.unit if op == "*" else a.unit / b.unit)
        # comparisons
        if a.kind == "any" or b.kind == "any":
            return T_BOOL
        if a.kind == "time" and b.kind == "time":
            return T_BOOL
        if a.kind == "quantity":
            _compat_q(a, b, op)
            return T_BOOL
        if a.kind == b.kind and a.kind in ("str", "bool"):
            if op not in ("==", "!="):
                raise ExprError(f"ordering comparison on {a.kind}")
            if a.kind == "str" and a.values and b.values and len(b.values) == 1 and not (b.values <= a.values):
                raise ExprError(f"comparison with {sorted(b.values)} which is not among {sorted(a.values)}")
            if a.kind == "str" and a.values and b.values and len(a.values) == 1 and not (a.values <= b.values):
                raise ExprError(f"comparison with {sorted(a.values)} which is not among {sorted(b.values)}")
            return T_BOOL
        raise ExprError(f"cannot compare {a.describe()} with {b.describe()}")
    if tag == "call":
        return _infer_call(n[1], n[2], tenv)
    raise ExprError(f"bad node {n!r}")


def _infer_call(fn: str, args, tenv: TypeEnv) -> T:
    ts = [_infer(a, tenv) for a in args] if fn not in ("field", "time", "to") else None

    def arity(k):
        if len(args) != k:
            raise ExprError(f"{fn}() takes {k} arguments")

    if fn == "if":
        arity(3)
        _need_bool(ts[0], "if condition")
        a, b = ts[1], ts[2]
        if a.kind == "any":
            return b
        if b.kind == "any":
            return a
        if a.kind != b.kind:
            raise ExprError(f"if() branches differ: {a.describe()} vs {b.describe()}")
        if a.kind == "quantity":
            _compat_q(a, b, "if")
            return a if not a.zero_literal else b
        if a.kind == "str":
            return T_STR((a.values or frozenset()) | (b.values or frozenset()) or None)
        return a
    if fn == "is_known":
        arity(1)
        return T_BOOL
    if fn == "coalesce":
        arity(2)
        a, b = ts
        if a.kind == "any":
            return b
        if b.kind != "any" and a.kind != b.kind:
            raise ExprError(f"coalesce() types differ: {a.describe()} vs {b.describe()}")
        if a.kind == "quantity":
            _compat_q(a, b, "coalesce")
        return a
    if fn == "field":
        arity(2)
        ent = _infer(args[0], tenv)
        if args[1][0] != "str":
            raise ExprError("field(entity, 'var') requires a literal var name")
        if ent.kind == "any":
            return T_ANY
        if ent.kind != "str" or not ent.values:
            raise ExprError("field() needs an enum-typed entity argument so every target can be checked")
        types = {v: tenv.lookup_type(f"{v}.{args[1][1]}") for v in sorted(ent.values)}
        first = next(iter(types.values()))
        for v, t in types.items():
            if t.kind != first.kind or (t.kind == "quantity" and not t.unit.same_dimension(first.unit)):
                raise ExprError(f"field(..., '{args[1][1]}') has inconsistent types across entities ({v})")
        return first
    if fn == "time":
        arity(1)
        if args[0][0] != "str":
            raise ExprError("time() requires an ISO string literal")
        try:
            parse_dt(args[0][1])
        except ValueError as e:
            raise ExprError(str(e)) from e
        return T_TIME
    if fn == "to":
        arity(2)
        t = _infer(args[0], tenv)
        if args[1][0] != "str":
            raise ExprError("to(x, 'unit') requires a literal unit")
        try:
            target = parse_unit(args[1][1])
        except UnitError as e:
            raise ExprError(str(e)) from e
        if t.kind == "any":
            return T_Q(target)
        _compat_q(t, T_Q(target), "to")
        return T_Q(target)
    if fn == "count":
        for t in ts:
            _need_bool(t, "count")
        return T_Q(DIMENSIONLESS)
    if fn in ("min", "max", "clamp"):
        if fn == "clamp":
            arity(3)
        if not ts:
            raise ExprError(f"{fn}() needs arguments")
        base = next((t for t in ts if t.kind != "any" and not t.zero_literal), ts[0])
        for t in ts:
            _compat_q(base, t, fn)
        return base
    if fn in ("abs", "floor", "ceil", "round"):
        arity(1)
        if ts[0].kind not in ("quantity", "any"):
            raise ExprError(f"{fn}() of {ts[0].describe()}")
        return ts[0]
    raise ExprError(f"unknown function {fn}")


# ---------------------------------------------------------------------------
# JSON <-> typed value conversion
# ---------------------------------------------------------------------------

def to_typed(raw: Any, var_type: str, unit: str | None) -> Any:
    """Convert a stored JSON value into an evaluator value."""
    if raw is None or raw == UNKNOWN_JSON:
        return UNKNOWN
    if var_type == "quantity":
        return Q(float(raw), parse_unit(unit))
    if var_type == "timestamp":
        return parse_dt(raw)
    return raw


def to_json(value: Any, var_type: str, unit: str | None) -> Any:
    """Convert an evaluator value into a stored JSON value of the declared type/unit."""
    if value is UNKNOWN:
        return UNKNOWN_JSON
    if var_type == "quantity":
        if not isinstance(value, Q):
            raise EvalError(f"expected quantity for {unit}, got {type(value).__name__}")
        target = parse_unit(unit)
        if _is_zero_literal(value):
            return 0.0
        try:
            v = value.to(target).value
        except UnitError as e:
            raise EvalError(str(e)) from e
        if not math.isfinite(v):
            raise EvalError("non-finite quantity")
        return float(v)
    if var_type == "timestamp":
        if not isinstance(value, datetime):
            raise EvalError("expected timestamp")
        return value.isoformat()
    if var_type == "boolean":
        if not isinstance(value, bool):
            raise EvalError("expected boolean")
        return value
    if var_type in ("enum", "text"):
        if not isinstance(value, str):
            raise EvalError("expected string")
        return value
    return value


def value_to_display(value: Any) -> Any:
    """Human/JSON-friendly rendering for observations and traces."""
    if value is UNKNOWN:
        return UNKNOWN_JSON
    if isinstance(value, Q):
        return {"value": round(value.value, 6), "unit": value.unit.label}
    if isinstance(value, datetime):
        return value.isoformat()
    return value
