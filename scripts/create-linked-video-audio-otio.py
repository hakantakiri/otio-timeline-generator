#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import opentimelineio as otio


# How this script works
# ---------------------
# This is a focused DaVinci Resolve OTIO interoperability script. It receives a
# single video file, probes it with ffprobe only to read the source duration and
# detect whether an embedded audio stream exists, then writes a minimal OTIO
# file.
#
# When audio exists, the output timeline contains two clips:
# - one video clip on V1;
# - one audio clip on A1.
#
# Both clips point to the same source media file, use the same source time range,
# and carry the same Resolve-specific metadata:
#
#     metadata["Resolve_OTIO"]["Link Group ID"] = <id>
#
# In Resolve 21, this importer metadata makes the video and audio clips appear
# as a linked pair. This is Resolve-specific behavior, not a portable OTIO
# standard for linked clips.
#
# When no embedded audio exists, the script writes a video-only OTIO and does not
# add Resolve link metadata because there is no audio clip to link.
#
# The default file URL format keeps local path spaces unescaped because DaVinci
# Resolve can fail to relink percent-encoded local paths such as "dji%20neo".
# Use --file-url-mode encoded when you need standards-compliant file URIs for
# OTIO tooling outside Resolve.


VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".mts",
    ".m2ts",
}

FileUrlMode = Literal["resolve", "encoded"]


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


def run_ffprobe(source: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Install FFmpeg before running this script.")

    command = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def parse_duration_seconds(probe_data: dict) -> float:
    duration = probe_data.get("format", {}).get("duration")
    if duration is not None:
        parsed = float(duration)
        if math.isfinite(parsed) and parsed > 0:
            return parsed

    stream_durations = []
    for stream in probe_data.get("streams", []):
        duration = stream.get("duration")
        if duration is None:
            continue
        parsed = float(duration)
        if math.isfinite(parsed) and parsed > 0:
            stream_durations.append(parsed)

    if not stream_durations:
        raise RuntimeError("ffprobe did not report a usable source duration.")

    return max(stream_durations)


def has_embedded_audio(probe_data: dict) -> bool:
    return any(
        stream.get("codec_type") == "audio"
        for stream in probe_data.get("streams", [])
    )


def time_range_for_source(duration_seconds: float, rate: float) -> otio.opentime.TimeRange:
    duration_frames = max(1, math.ceil(duration_seconds * rate))
    return otio.opentime.TimeRange(
        start_time=otio.opentime.RationalTime(0, rate),
        duration=otio.opentime.RationalTime(duration_frames, rate),
    )


def file_url_for_source(source: Path, mode: FileUrlMode) -> str:
    if mode == "resolve":
        return "file://" + str(source)
    if mode == "encoded":
        return source.as_uri()
    raise ValueError(f"Unsupported file URL mode: {mode}")


def media_reference_for_source(
    source: Path,
    available_range: otio.opentime.TimeRange,
    file_url_mode: FileUrlMode,
) -> otio.schema.ExternalReference:
    return otio.schema.ExternalReference(
        target_url=file_url_for_source(source, file_url_mode),
        available_range=available_range,
    )


def clip_for_source(
    source: Path,
    media_reference: otio.schema.ExternalReference,
    source_range: otio.opentime.TimeRange,
    link_group_id: int | None,
) -> otio.schema.Clip:
    metadata = {}
    if link_group_id is not None:
        metadata["Resolve_OTIO"] = {
            "Link Group ID": link_group_id,
        }

    return otio.schema.Clip(
        name=source.name,
        media_reference=media_reference,
        source_range=source_range,
        metadata=metadata,
    )


def build_timeline(
    source: Path,
    timeline_name: str,
    time_rate: float,
    link_group_id: int,
    file_url_mode: FileUrlMode,
) -> tuple[otio.schema.Timeline, int, bool]:
    probe_data = run_ffprobe(source)
    source_has_audio = has_embedded_audio(probe_data)

    source_range = time_range_for_source(
        duration_seconds=parse_duration_seconds(probe_data),
        rate=time_rate,
    )

    video_track = otio.schema.Track(name="V1", kind=otio.schema.TrackKind.Video)
    video_clip = clip_for_source(
        source=source,
        media_reference=media_reference_for_source(source, source_range, file_url_mode),
        source_range=source_range,
        link_group_id=link_group_id if source_has_audio else None,
    )
    video_track.append(video_clip)

    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.tracks.append(video_track)

    if source_has_audio:
        audio_track = otio.schema.Track(name="A1", kind=otio.schema.TrackKind.Audio)
        audio_clip = clip_for_source(
            source=source,
            media_reference=media_reference_for_source(
                source,
                source_range,
                file_url_mode,
            ),
            source_range=source_range,
            link_group_id=link_group_id,
        )
        audio_track.append(audio_clip)
        timeline.tracks.append(audio_track)

    return timeline, int(source_range.duration.value), source_has_audio


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create a minimal OTIO file that imports into DaVinci Resolve as "
            "one linked video/audio clip pair when embedded audio exists."
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Video file. Files without embedded audio generate video-only OTIO.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output .otio path.",
    )
    parser.add_argument(
        "--timeline-name",
        default="Linked Video Audio Test",
        help="Timeline name stored inside the OTIO file.",
    )
    parser.add_argument(
        "--time-rate",
        type=positive_float,
        default=24.0,
        help=(
            "OTIO timing rate used to express clip duration. This is not what "
            "links the clips. Default: 24."
        ),
    )
    parser.add_argument(
        "--resolve-link-group-id",
        type=positive_int,
        default=2,
        help=(
            "Resolve_OTIO Link Group ID written to the video/audio pair when "
            "embedded audio exists. Default: 2."
        ),
    )
    parser.add_argument(
        "--file-url-mode",
        choices=("resolve", "encoded"),
        default="resolve",
        help=(
            "How local file paths are written into OTIO target_url values. "
            "'resolve' keeps spaces unescaped for DaVinci Resolve compatibility; "
            "'encoded' uses standard percent-encoded file URIs. Default: resolve."
        ),
    )

    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Source does not exist: {source}")
    if not source.is_file():
        raise ValueError(f"Source is not a file: {source}")
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(f"Source does not look like a supported video file: {source}")
    if output.suffix.lower() != ".otio":
        raise ValueError("Output path must end with .otio")

    timeline, duration_frames, source_has_audio = build_timeline(
        source=source,
        timeline_name=args.timeline_name,
        time_rate=args.time_rate,
        link_group_id=args.resolve_link_group_id,
        file_url_mode=args.file_url_mode,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(output))

    if source_has_audio:
        print(f"Generated linked video/audio OTIO: {output}")
    else:
        print(f"Generated video-only OTIO: {output}")
        print("Warning: no embedded audio found; no Resolve link metadata was written.")
    print(f"Source: {source}")
    print("Tracks: V1 video, A1 audio" if source_has_audio else "Tracks: V1 video")
    print(f"Duration: {duration_frames} frames at {args.time_rate:g}")
    print(f"File URL mode: {args.file_url_mode}")
    if source_has_audio:
        print(f"Resolve Link Group ID: {args.resolve_link_group_id}")


if __name__ == "__main__":
    main()
