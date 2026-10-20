"""Command line: ``udse serve`` (API + mobile web client), ``udse demo`` (end-to-end walkthrough)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _serve(args: argparse.Namespace) -> None:
    import uvicorn

    from .api.app import create_app
    from .engine import Engine

    os.environ.setdefault("UDSE_DB", args.db)
    eng = Engine(args.db, use_env_llm=args.llm)
    app = create_app(eng, dev_mode=True, seed_demo=not args.no_demo, cors_origins=args.cors_origin or None)
    print(f"UDSE v0.5 serving on http://{args.host}:{args.port}  (db={args.db}, llm={'on' if eng.llm else 'offline'})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def _demo(args: argparse.Namespace) -> None:
    from .common import parse_dt
    from .engine import Engine
    from .model_ir.schemas import DecisionSpec
    from .trajectory_analytics.analytics import Analytics

    eng = Engine(args.db)
    eng.seed_fixtures()
    fixture = {"restaurant": ("restaurant/decision.json", "user_a", "2026-10-24T12:30:00-07:00"),
               "fundraising": ("fundraising/decision.json", "user_fa", "2026-10-18T09:00:00-07:00"),
               "garden": ("new_domain/decision.json", "user_g1", "2026-11-01T09:00:00-07:00")}[args.fixture]
    spec = DecisionSpec.model_validate_json((eng.fixtures_dir / fixture[0]).read_text())
    eng.create_decision(spec, fixture[1])
    res = eng.build_model(spec.decision_id, parse_dt(fixture[2]))
    s = res.summary()
    print(f"\n== {spec.question}\nModel: {s['status']} via {s['path']}  hash={s['model_ref']['hash'][:19] if s['model_ref'] else None}")
    print("Metric fidelity:", json.dumps(s["report"]["metric_fidelity"]))
    for lim in s["limitations"]:
        print("  limitation:", lim)
    if res.model is None:
        sys.exit(1)
    plan = eng.orchestrator.plan(spec.decision_id)
    out = eng.orchestrator.execute(plan)
    an = Analytics(eng.store)
    print(f"\nScreening runs ({len(out['screening_runs'])}) and detailed runs ({len(out['depth_runs'])}):")
    for r in out["screening_runs"] + out["depth_runs"]:
        print(f"  [{r['stage']:9}] {r['label'][:58]:58} {r['status']:18} {str(r['outcome'] or ''):30} last={r['last_simulated_at'][:16]}")
    if out["depth_runs"]:
        rid = out["depth_runs"][0]["run_id"]
        rep = an.replay(rid)
        print(f"\nReplay of {rid}: event-log ok={rep['event_log']['ok']} transition ok={rep.get('transition', {}).get('ok')} llm_calls=0")
    print("\n" + out["note"])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="udse", description="Universal Decision Simulation Engine v0.5")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API and the mobile-first web client")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--db", default=str(Path("var") / "udse.sqlite3"))
    s.add_argument("--no-demo", action="store_true", help="do not seed and run the fixture decisions")
    s.add_argument("--llm", action="store_true", help="use Claude when UDSE_LLM=anthropic and credentials are configured")
    s.add_argument("--cors-origin", action="append", metavar="ORIGIN",
                   help="allow this origin to call the API cross-origin (repeatable; default: UDSE_CORS_ORIGINS or none)")
    s.set_defaults(fn=_serve)
    d = sub.add_parser("demo", help="run one fixture end to end in the terminal")
    d.add_argument("fixture", choices=["restaurant", "fundraising", "garden"])
    d.add_argument("--db", default=":memory:")
    d.set_defaults(fn=_demo)
    args = ap.parse_args(argv)
    if args.cmd == "serve":
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    args.fn(args)


if __name__ == "__main__":
    main()
