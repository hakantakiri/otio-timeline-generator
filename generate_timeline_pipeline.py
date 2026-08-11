#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path


DEFAULT_TIMELINE_NAME = "Generated Resolve Timeline"
DEFAULT_FPS = 24.0
DEFAULT_IMAGE_DURATION_SECONDS = 3.0


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def script_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "scripts" / name


def run_step(command: list[str]) -> int:
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, text=True)
    if result.returncode != 0 and result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    return result.returncode


def append_if_enabled(command: list[str], flag: str, enabled: bool) -> None:
    if enabled:
        command.append(flag)


def artifact_paths(artifact_dir: Path) -> tuple[Path, Path, Path]:
    return (
        artifact_dir / "media_manifest.json",
        artifact_dir / "validation_report.json",
        artifact_dir / "track_plan.json",
    )


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def run_pipeline(args: argparse.Namespace, artifact_dir: Path, keep_artifacts: bool) -> int:
    manifest, validation_report, track_plan = artifact_paths(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    scan_command = [
        sys.executable,
        str(script_path("scan-media.py")),
        str(args.input_root),
        "--output",
        str(manifest),
        "--fps",
        f"{args.fps:g}",
        "--image-duration",
        f"{args.image_duration:g}",
    ]
    append_if_enabled(scan_command, "--recursive", args.recursive)
    append_if_enabled(scan_command, "--strict", args.strict)
    append_if_enabled(scan_command, "--quiet", args.quiet)
    exit_code = run_step(scan_command)
    if exit_code != 0:
        return exit_code

    validate_command = [
        sys.executable,
        str(script_path("validate-media-names.py")),
        "--manifest",
        str(manifest),
    ]
    if keep_artifacts:
        validate_command.extend(["--output", str(validation_report)])
    exit_code = run_step(validate_command)
    if exit_code == 2 and not args.allow_stem_collisions:
        return exit_code
    if exit_code not in {0, 2}:
        return exit_code

    plan_command = [
        sys.executable,
        str(script_path("plan-timeline-tracks.py")),
        "--manifest",
        str(manifest),
        "--output",
        str(track_plan),
        "--layout-mode",
        args.layout_mode,
        "--audio",
        args.audio,
        "--resolve-layout",
        args.resolve_layout,
        "--resolve-link-start-id",
        str(args.resolve_link_start_id),
    ]
    exit_code = run_step(plan_command)
    if exit_code != 0:
        return exit_code

    write_command = [
        sys.executable,
        str(script_path("write-otio-timeline.py")),
        "--manifest",
        str(manifest),
        "--track-plan",
        str(track_plan),
        "--output",
        str(args.output),
        "--timeline-name",
        args.timeline_name,
        "--file-url-mode",
        args.file_url_mode,
    ]
    append_if_enabled(write_command, "--allow-stem-collisions", args.allow_stem_collisions)
    append_if_enabled(write_command, "--quiet", args.quiet)
    exit_code = run_step(write_command)
    if exit_code != 0:
        return exit_code

    manifest_data = load_json(manifest)
    plan_data = load_json(track_plan)
    summary = plan_data["summary"]

    print(f"Generated: {args.output}")
    print(f"Timeline: {args.timeline_name}")
    print(f"Source items: {summary['source_item_count']}")
    print(f"Visual clips: {summary['visual_clip_count']}")
    print(f"Linked audio clips: {summary['linked_audio_clip_count']}")
    print(f"Standalone audio clips: {summary['standalone_audio_clip_count']}")
    print(f"Tracks: {summary['track_count']}")
    print(f"FPS: {manifest_data['settings']['fps']:g}")
    print(f"Image duration: {manifest_data['settings']['image_duration_seconds']:g}s")
    print(f"Layout mode: {args.layout_mode}")
    print(f"Audio mode: {args.audio}")
    print(f"Resolve layout: {args.resolve_layout}")
    print(f"Resolve link start ID: {args.resolve_link_start_id}")
    print(f"File URL mode: {args.file_url_mode}")
    print(f"Skipped media: {manifest_data['summary']['skipped_count']}")

    if keep_artifacts:
        print(f"Artifacts: {artifact_dir}")
        print(f"Manifest: {manifest}")
        print(f"Validation report: {validation_report}")
        print(f"Track plan: {track_plan}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Resolve-oriented OTIO timeline by orchestrating the "
            "chainable scripts in ./scripts."
        )
    )
    parser.add_argument("input_root", type=Path, help="Root folder containing source media folders.")
    parser.add_argument("--output", type=Path, required=True, help="Output .otio path.")
    parser.add_argument("--timeline-name", default=DEFAULT_TIMELINE_NAME, help="Timeline name.")
    parser.add_argument("--fps", type=positive_float, default=DEFAULT_FPS, help="OTIO timing rate. Default: 24.")
    parser.add_argument(
        "--image-duration",
        type=positive_float,
        default=DEFAULT_IMAGE_DURATION_SECONDS,
        help="Still image duration in seconds. Default: 3.",
    )
    parser.add_argument("--recursive", action="store_true", help="Scan nested folders.")
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
    parser.add_argument(
        "--file-url-mode",
        choices=("resolve", "encoded"),
        default="resolve",
        help="resolve keeps spaces unescaped; encoded uses percent-encoded file URIs. Default: resolve.",
    )
    parser.add_argument(
        "--allow-stem-collisions",
        action="store_true",
        help="Generate even when same-folder files share a filename stem.",
    )
    parser.add_argument("--strict", action="store_true", help="Fail on first media probe error.")
    parser.add_argument("--quiet", action="store_true", help="Suppress child script progress output.")
    parser.add_argument("--keep-artifacts", action="store_true", help="Keep intermediate JSON artifacts.")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help=(
            "Directory for media_manifest.json, validation_report.json, and "
            "track_plan.json. Implies --keep-artifacts."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.input_root = args.input_root.expanduser().resolve()
    args.output = args.output.expanduser().resolve()

    if not args.input_root.exists():
        raise FileNotFoundError(f"Input root does not exist: {args.input_root}")
    if not args.input_root.is_dir():
        raise ValueError(f"Input root is not a directory: {args.input_root}")
    if args.output.suffix.lower() != ".otio":
        raise ValueError("Output file must end with .otio")

    if args.artifact_dir is not None:
        artifact_dir = args.artifact_dir.expanduser().resolve()
        return run_pipeline(args, artifact_dir, keep_artifacts=True)

    if args.keep_artifacts:
        artifact_dir = args.output.with_suffix("")
        artifact_dir = artifact_dir.with_name(f"{artifact_dir.name}_pipeline_artifacts")
        return run_pipeline(args, artifact_dir, keep_artifacts=True)

    with tempfile.TemporaryDirectory(prefix="otio_timeline_pipeline_") as temp_dir:
        return run_pipeline(args, Path(temp_dir), keep_artifacts=False)


if __name__ == "__main__":
    sys.exit(main())
