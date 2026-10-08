from __future__ import annotations
import argparse
import json
from pathlib import Path
from .pipeline import execute, sha


def main():
    parser = argparse.ArgumentParser(description="Reference video only -> autonomous story, assets, footage and edited film")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Storage path only, not a creative input")
    parser.add_argument("--stage", choices=("all", "screenplay", "reedit"), default="all",
                        help="all: full chain; screenplay: text only; reedit: explicit new bounded edit of existing media")
    parser.add_argument("--unlimited-image-jobs", action="store_true",
                        help="Explicit spending authority: remove image count cap, keep repair and one-submit rules")
    args = parser.parse_args()
    if args.unlimited_image_jobs and args.stage != "all":
        parser.error("--unlimited-image-jobs applies only to the full production chain")
    output = args.output or Path("runs") / ("video_" + sha(args.video)[:12])
    if args.stage == "reedit":
        from .production import reedit_existing_media
        result = reedit_existing_media(args.video, output)
    elif args.stage == "all":
        from .production import full_run
        result = full_run(args.video, output, unlimited_image_jobs=args.unlimited_image_jobs)
    else:
        result = execute(args.video, output)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["status"] in ("model_checked_screenplay_candidate", "screenplay_needs_review",
                                    "model_checked_final_video", "video_candidate_with_limitations") else 2


if __name__ == "__main__":
    raise SystemExit(main())
