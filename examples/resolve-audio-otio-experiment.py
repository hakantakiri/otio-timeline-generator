#!/usr/bin/env python3

from __future__ import annotations

import argparse
import importlib.util
import math
import shutil
import sys
from pathlib import Path

import opentimelineio as otio


def load_generate_timeline_module():
    module_path = Path(__file__).with_name("generate-timeline.py")
    spec = importlib.util.spec_from_file_location("generate_timeline", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load helper module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc

    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")

    return parsed


def audio_stream_by_index(
    audio_streams: list[dict],
    requested_stream_index: int | None,
) -> dict:
    if requested_stream_index is None:
        return audio_streams[0]

    for stream in audio_streams:
        if stream.get("index") == requested_stream_index:
            return stream

    available = ", ".join(str(stream.get("index")) for stream in audio_streams)
    raise ValueError(
        f"Audio stream index {requested_stream_index} was not found. "
        f"Available audio stream indexes: {available}"
    )


def build_experiment_timeline(
    source: Path,
    output: Path,
    fps: float,
    timeline_name: str,
    requested_audio_stream_index: int | None,
    resolve_link_group_id: int | None,
) -> tuple[otio.schema.Timeline, object, object]:
    helper = load_generate_timeline_module()

    if not helper.is_video(source):
        raise ValueError(f"Source must be a supported video file: {source}")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Install ffmpeg first.")

    exiftool = shutil.which("exiftool")
    data = helper.ffprobe_media_info(source, ffprobe)
    visual_stream = helper.first_visual_stream(data, source)
    embedded_audio_streams = helper.audio_streams(data)

    if not embedded_audio_streams:
        raise RuntimeError(f"No embedded audio streams found in: {source}")

    audio_stream = audio_stream_by_index(
        embedded_audio_streams,
        requested_audio_stream_index,
    )
    sort_time = helper.probe_sort_time(source, exiftool)
    folder_name = source.parent.name

    visual_item = helper.make_visual_media_item(
        path=source,
        folder_name=folder_name,
        data=data,
        stream=visual_stream,
        embedded_audio_streams=embedded_audio_streams,
        sort_time=sort_time,
        timeline_fps=fps,
        image_duration_seconds=helper.DEFAULT_IMAGE_DURATION_SECONDS,
    )
    audio_item = helper.make_audio_media_item(
        path=source,
        folder_name=folder_name,
        data=data,
        stream=audio_stream,
        sort_time=sort_time,
        timeline_fps=fps,
        stream_role="embedded_audio",
        visual_duration_seconds=visual_item.source_duration_seconds,
        visual_duration_frames=visual_item.duration_frames,
    )

    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.global_start_time = helper.rt(0, fps)
    timeline.metadata["folder_to_otio"] = {
        "experiment": "resolve_embedded_audio_duplicate_reference",
        "source_path": str(source.resolve()),
        "output_path": str(output.resolve()),
        "timeline_fps": fps,
        "audio_stream_index": audio_item.stream_index,
        "audio_profile": audio_item.audio_profile,
        "resolve_link_group_id": resolve_link_group_id,
        "warning": (
            "This file intentionally references the same video container on a "
            "video track and an audio track. It is for DaVinci Resolve import "
            "compatibility testing only."
        ),
    }

    video_track = otio.schema.Track(
        name=f"{folder_name} - Video - {visual_item.aspect_ratio}",
        kind=otio.schema.TrackKind.Video,
    )
    audio_track = otio.schema.Track(
        name=f"{folder_name} - Audio - {audio_item.audio_profile}",
        kind=otio.schema.TrackKind.Audio,
    )

    video_clip = helper.make_clip(visual_item, fps)
    audio_clip = helper.make_clip(audio_item, fps)

    if resolve_link_group_id is not None:
        video_clip.metadata["Resolve_OTIO"] = {
            "Link Group ID": resolve_link_group_id,
        }
        audio_clip.metadata["Resolve_OTIO"] = {
            "Link Group ID": resolve_link_group_id,
        }
        video_clip.metadata["folder_to_otio_linking"] = {
            "link_group_id": resolve_link_group_id,
            "link_role": "video",
            "link_target": "embedded_audio",
        }
        audio_clip.metadata["folder_to_otio_linking"] = {
            "link_group_id": resolve_link_group_id,
            "link_role": "audio",
            "link_target": "embedded_video",
        }

    video_track.append(video_clip)
    audio_track.append(audio_clip)

    timeline.tracks.append(video_track)
    timeline.tracks.append(audio_track)

    return timeline, visual_item, audio_item


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a minimal OTIO file that references one video file on both "
            "a video track and an audio track. This is an experimental DaVinci "
            "Resolve compatibility test, not the production generator."
        )
    )
    parser.add_argument(
        "source",
        type=Path,
        help="One supported video file with embedded audio.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output .otio file for the experiment.",
    )
    parser.add_argument(
        "--fps",
        type=positive_float,
        default=24.0,
        help="Timeline timing rate. Default: 24.",
    )
    parser.add_argument(
        "--timeline-name",
        default="Resolve Embedded Audio OTIO Experiment",
        help="Timeline name inside the OTIO file.",
    )
    parser.add_argument(
        "--audio-stream-index",
        type=int,
        default=None,
        help="Specific ffprobe audio stream index to use. Defaults to the first audio stream.",
    )
    parser.add_argument(
        "--resolve-link-group-id",
        type=int,
        default=None,
        help=(
            "Experimental Resolve metadata value. When set, writes "
            "Resolve_OTIO {'Link Group ID': value} onto both generated clips."
        ),
    )

    args = parser.parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Source does not exist: {source}")

    if output.suffix.lower() != ".otio":
        raise ValueError("Output file should end with .otio")

    timeline, visual_item, audio_item = build_experiment_timeline(
        source=source,
        output=output,
        fps=args.fps,
        timeline_name=args.timeline_name,
        requested_audio_stream_index=args.audio_stream_index,
        resolve_link_group_id=args.resolve_link_group_id,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    otio.adapters.write_to_file(timeline, str(output))

    print(f"Generated experiment OTIO: {output}")
    print(f"Source: {source}")
    print("Tracks: 1 video, 1 audio")
    print(f"Duration frames: {visual_item.duration_frames}")
    print(f"Duration seconds: {visual_item.source_duration_seconds:.6f}")
    print(f"Video aspect ratio: {visual_item.aspect_ratio}")
    print(f"Audio stream index: {audio_item.stream_index}")
    print(f"Audio profile: {audio_item.audio_profile}")
    print(f"Resolve link group ID: {args.resolve_link_group_id}")
    print(
        "Warning: this intentionally duplicates the same media reference on "
        "video and audio tracks for Resolve import testing."
    )


if __name__ == "__main__":
    main()
