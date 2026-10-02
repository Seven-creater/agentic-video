from __future__ import annotations
import argparse
import json
from pathlib import Path
from .pipeline import execute, sha


def main():
    parser = argparse.ArgumentParser(description="Reference video only -> autonomous story, assets, footage and edited film")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Storage path only, not a creative input")
    parser.add_argument("--stage", choices=("all", "screenplay"), default="all",
                        help="Execution boundary only; default runs assets, materials and model editing too")
    args = parser.parse_args()
    output = args.output or Path("runs") / ("video_" + sha(args.video)[:12])
    if args.stage == "all":
        from .production import full_run
        result = full_run(args.video, output)
    else:
        result = execute(args.video, output)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] in ("model_checked_screenplay_candidate", "screenplay_needs_review",
                                    "model_checked_final_video", "video_candidate_with_limitations") else 2


if __name__ == "__main__":
    raise SystemExit(main())
