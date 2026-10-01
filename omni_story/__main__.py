from __future__ import annotations
import argparse
import json
from pathlib import Path
from .pipeline import execute, sha


def main():
    parser = argparse.ArgumentParser(description="Reference video only -> autonomous rough production screenplay")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Storage path only, not a creative input")
    args = parser.parse_args()
    output = args.output or Path("runs") / ("video_" + sha(args.video)[:12])
    result = execute(args.video, output)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] == "model_checked_screenplay_candidate" else 2


if __name__ == "__main__":
    raise SystemExit(main())
