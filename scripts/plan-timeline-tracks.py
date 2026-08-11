#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

import timeline_pipeline_core as core


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a chainable timeline track plan JSON file from a media manifest."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Input media manifest JSON path.")
    parser.add_argument("--output", type=Path, required=True, help="Output track plan JSON path.")
    parser.add_argument(
        "--layout-mode",
        choices=("global-sequence", "per-track-sequence"),
        default="global-sequence",
        help="Timeline layout strategy. Default: global-sequence.",
    )
    parser.add_argument(
        "--audio",
        choices=("linked", "standalone", "none"),
        default="linked",
        help="Audio handling. Default: linked.",
    )
    parser.add_argument(
        "--resolve-layout",
        choices=("organized", "linked-pairs"),
        default="organized",
        help="Resolve track compatibility layout. Default: organized.",
    )
    parser.add_argument(
        "--resolve-link-start-id",
        type=positive_int,
        default=2,
        help="First Resolve_OTIO Link Group ID. Default: 2.",
    )
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("Output file must end with .json")

    manifest, items, _ = core.load_manifest(manifest_path)
    plan = core.track_plan_from_items(
        manifest=manifest,
        items=items,
        layout_mode=args.layout_mode,
        audio_mode=args.audio,
        resolve_layout=args.resolve_layout,
        resolve_link_start_id=args.resolve_link_start_id,
    )
    if not plan["clips"]:
        raise RuntimeError("No timeline clips could be planned from the manifest.")

    core.write_json(output, plan)

    print(f"Track plan: {output}")
    print(f"Source items: {plan['summary']['source_item_count']}")
    print(f"Clips: {plan['summary']['clip_count']}")
    print(f"Visual clips: {plan['summary']['visual_clip_count']}")
    print(f"Linked audio clips: {plan['summary']['linked_audio_clip_count']}")
    print(f"Standalone audio clips: {plan['summary']['standalone_audio_clip_count']}")
    print(f"Tracks: {plan['summary']['track_count']}")
    print(f"Layout mode: {args.layout_mode}")
    print(f"Audio mode: {args.audio}")


if __name__ == "__main__":
    main()
