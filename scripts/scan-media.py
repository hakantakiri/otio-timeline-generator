#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
from pathlib import Path

import timeline_pipeline_core as core


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan media folders and write a chainable media manifest JSON file."
    )
    parser.add_argument("input_root", type=Path, help="Root folder containing source media folders.")
    parser.add_argument("--output", type=Path, required=True, help="Output media manifest JSON path.")
    parser.add_argument("--fps", type=positive_float, default=core.DEFAULT_FPS, help="Timeline timing rate. Default: 24.")
    parser.add_argument(
        "--image-duration",
        type=positive_float,
        default=core.DEFAULT_IMAGE_DURATION_SECONDS,
        help="Still image duration in seconds. Default: 3.",
    )
    parser.add_argument("--recursive", action="store_true", help="Scan nested folders.")
    parser.add_argument("--strict", action="store_true", help="Fail on first media probe error.")
    parser.add_argument("--quiet", action="store_true", help="Suppress progress output.")
    args = parser.parse_args()

    input_root = args.input_root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    progress = core.ProgressReporter(quiet=args.quiet)

    if not input_root.exists():
        raise FileNotFoundError(f"Input root does not exist: {input_root}")
    if not input_root.is_dir():
        raise ValueError(f"Input root is not a directory: {input_root}")
    if output.suffix.lower() != ".json":
        raise ValueError("Output file must end with .json")

    items, skipped, source_folders = core.build_source_items(
        input_root=input_root,
        fps=args.fps,
        image_duration_seconds=args.image_duration,
        recursive=args.recursive,
        strict=args.strict,
        progress=progress,
    )
    if not items:
        raise RuntimeError(f"No supported media found under: {input_root}")

    manifest = core.manifest_from_scan(
        input_root=input_root,
        source_folders=source_folders,
        items=items,
        skipped=skipped,
        fps=args.fps,
        image_duration_seconds=args.image_duration,
        recursive=args.recursive,
        strict=args.strict,
    )
    core.write_json(output, manifest)

    print(f"Manifest: {output}")
    print(f"Source folders: {len(source_folders)}")
    print(f"Source items: {len(items)}")
    print(f"Skipped media: {len(skipped)}")
    print(f"Images: {manifest['summary']['image_count']}")
    print(f"Videos: {manifest['summary']['video_count']}")
    print(f"Audio: {manifest['summary']['audio_count']}")


if __name__ == "__main__":
    main()
