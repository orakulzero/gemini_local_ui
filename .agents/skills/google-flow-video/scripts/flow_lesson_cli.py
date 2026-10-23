#!/usr/bin/env python3
"""CLI utility for Google Flow Educational Video Studio storyboard and lesson assembly."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.bridge.flow import FlowClient  # noqa: E402


async def _run_async(args: argparse.Namespace) -> Dict[str, Any]:
    client = FlowClient()
    project = await client.generate_educational_storyboard(
        topic=args.topic,
        scene_count=args.scenes,
        language=args.language,
        target_audience=args.audience,
        aspect_ratio=args.aspect_ratio,
        source_context=args.context,
    )
    if args.dry_run and not args.assemble:
        return project.to_dict()

    if args.assemble:
        assembled = await client.assemble_educational_video(project.project_id)
        return assembled["project"]

    return project.to_dict()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="flow_lesson_cli",
        description="Google Flow Educational Video Studio CLI",
    )
    parser.add_argument("--topic", required=True, help="Educational lesson topic")
    parser.add_argument(
        "--scenes", type=int, default=4, help="Number of scenes (1..12)"
    )
    parser.add_argument(
        "--audience",
        default="intermediate",
        help="Target audience (beginner, intermediate, university)",
    )
    parser.add_argument(
        "--aspect-ratio", default="16:9", help="Video aspect ratio (16:9, 9:16, 1:1)"
    )
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument(
        "--context", default=None, help="Optional source context or notes"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate storyboard JSON without rendering MP4 clips",
    )
    parser.add_argument(
        "--assemble",
        action="store_true",
        help="Render all scene clips and assemble the full educational MP4 + subtitles",
    )

    parsed = parser.parse_args()
    result = asyncio.run(_run_async(parsed))
    sys.stdout.write(json.dumps(result, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
