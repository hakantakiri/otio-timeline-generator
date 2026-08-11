#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

import opentimelineio as otio

import timeline_pipeline_core as core


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write an OTIO timeline from chainable media manifest and track plan JSON files."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Input media manifest JSON path.")
    parser.add_argument("--track-plan", type=Path, required=True, help="Input track plan JSON path.")
    parser.add_argument("--output", type=Path, required=True, help="Output .otio path.")
    parser.add_argument("--timeline-name", default=core.DEFAULT_TIMELINE_NAME, help="Timeline name.")
    parser.add_argument(
        "--file-url-mode",
        choices=("resolve", "encoded"),
        default="resolve",
        help="resolve keeps spaces unescaped; encoded uses percent-encoded file URIs. Default: resolve.",
    )
    parser.add_argument(
        "--allow-stem-collisions",
        action="store_true",
        help="Write even when manifest contains Resolve-unsafe same-stem media names.",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    track_plan_path = args.track_plan.expanduser().resolve()
    output = args.output.expanduser().resolve()
    progress = core.ProgressReporter(quiet=args.quiet)

    if output.suffix.lower() != ".otio":
        raise ValueError("Output file must end with .otio")

    manifest, items, skipped = core.load_manifest(manifest_path)
    plan = core.load_track_plan(track_plan_path)
    collisions = core.find_stem_collisions(items)
    if collisions and not args.allow_stem_collisions:
        report = core.validation_report_for_items(items)
        for collision in report["collisions"]:
            print(f"Resolve-unsafe same-stem collision: {collision['folder']}/{collision['stem']}")
            for item in collision["items"]:
                print(f"  Current:  {item['path']}")
                print(f"  Proposed: {item['proposed_path']}")
        raise SystemExit(2)

    clips = core.clips_from_plan(items, plan)
    timeline = core.build_timeline_from_plan(
        manifest=manifest,
        plan=plan,
        skipped=skipped,
        clips=clips,
        timeline_name=args.timeline_name,
        file_url_mode=args.file_url_mode,
        progress=progress,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    progress.report(f"Writing OTIO file: {output}")
    otio.adapters.write_to_file(timeline, str(output))
    progress.report("Finished writing OTIO file.")

    print(f"Generated: {output}")
    print(f"Timeline: {args.timeline_name}")
    print(f"Source items: {len(items)}")
    print(f"Clips: {len(clips)}")
    print(f"Tracks: {len(timeline.tracks)}")
    print(f"File URL mode: {args.file_url_mode}")
    print(f"Skipped media: {len(skipped)}")


if __name__ == "__main__":
    main()
