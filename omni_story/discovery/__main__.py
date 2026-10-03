from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from ..pipeline import json_sha, sha, write
from .llm import model_config
from .state import State, session_lock


def main():
    parser = argparse.ArgumentParser(description="Local Qwen Douyin discovery -> verified reference -> Omni screenplay")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="Human login in a dedicated persistent local Chrome profile; no model calls")
    for name in ("run", "smoke"):
        command = commands.add_parser(name, help="Discover references" if name == "run" else "Paid Qwen local browser smoke test")
        command.add_argument("--output", type=Path, required=True, help="Reuse this directory to preserve the same session and budgets")
        if name == "run":
            command.add_argument("--stage", choices=("reference", "screenplay"), default="screenplay")
            command.add_argument("--continue-from-smoke", nargs="+", default=(), metavar="UNRESOLVED_QWEN_CALL_ID",
                                 help="Explicitly start new Douyin operations in the original smoke directory; retain unresolved calls and all budgets, never replay them")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Discovery requires Python 3.11+; the original pipeline still supports Python 3.10.")
    state = None
    lock = None
    try:
        from .browser import login, profile_path, run_browser
        if args.command == "login":
            asyncio.run(login())
            return 0
        config = {"kind": args.command, "browser_model": model_config(), "profile": str(profile_path()),
                  "omni_model": "qwen3.8-omni-flash", "selection_policy": "creative_utility_v1"}
        context = session_lock(args.output)
        context.__enter__()
        lock = context
        state = State(args.output, config, continue_from_smoke=getattr(args, "continue_from_smoke", ()))
        snapshot = {str(p.relative_to(Path(__file__).parents[2])): sha(p)
                    for p in sorted(Path(__file__).parent.rglob("*.py"))}
        shared_api = Path(__file__).parents[1] / "api.py"
        snapshot[str(shared_api.relative_to(Path(__file__).parents[2]))] = sha(shared_api)
        snapshot_path = state.output / "source_snapshots" / (json_sha(snapshot) + ".json")
        if not snapshot_path.exists():
            write(snapshot_path, snapshot)
        if not (state.output / "source_snapshot.json").exists():
            write(state.output / "source_snapshot.json", snapshot)
        if args.command == "smoke":
            from .fixture import smoke
            result = asyncio.run(smoke(state))
        else:
            result = asyncio.run(run_browser(state, stage=args.stage))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if result["status"] in {"browser_smoke_passed", "reference_selected", "model_checked_screenplay_candidate", "screenplay_needs_review"} else 2
    except (Exception, KeyboardInterrupt) as exc:
        result = {"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "blocked",
                  "reason": str(exc), "error_type": type(exc).__name__}
        if isinstance(exc, ModuleNotFoundError):
            result["reason"] = "Install optional dependencies: python -m pip install -e \".[discovery]\""
        if state is not None:
            write(state.output / "failure.json", result)
            with (state.output / "failures.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            result = state.result(**result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 2
    finally:
        if lock is not None:
            lock.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
