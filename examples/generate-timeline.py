#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

import opentimelineio as otio


# -----------------------------
# Defaults you will likely tweak
# -----------------------------

DEFAULT_TIMELINE_NAME = "Generated Trek Timeline"
DEFAULT_FPS = 24.0
DEFAULT_WIDTH = 3840
DEFAULT_HEIGHT = 2160
DEFAULT_IMAGE_DURATION_SECONDS = 3.0

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp"
}

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts"
}

AUDIO_EXTENSIONS = {
    ".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac"
}

MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

LayoutMode = Literal["global-sequence", "per-track-sequence"]
AudioMode = Literal["linked", "standalone", "none", "split-embedded"]
TrackKind = Literal["Video", "Audio"]
MediaType = Literal["Images", "Videos", "Audio"]
StreamRole = Literal["visual", "embedded_audio", "audio_only"]


@dataclass(frozen=True)
class MediaItem:
    path: Path
    folder_name: str
    track_kind: TrackKind
    media_type: MediaType
    stream_role: StreamRole
    stream_index: int | None
    grouping_label: str
    raw_width: int | None
    raw_height: int | None
    width: int | None
    height: int | None
    aspect_ratio: str | None
    sample_aspect_ratio: str | None
    display_aspect_ratio: str | None
    rotation_degrees: float | None
    codec_name: str | None
    audio_profile: str | None
    audio_channels: int | None
    audio_channel_layout: str | None
    audio_sample_rate: int | None
    embedded_audio_stream_count: int
    embedded_audio_profiles: list[str]
    sort_time: float
    duration_frames: int
    source_fps: float
    source_fps_label: str
    source_rate_units: str
    native_rate: float | None
    native_rate_label: str | None
    source_duration_seconds: float
    source_duration_frames: int
    timing_policy: str
    available_range: otio.opentime.TimeRange
    source_range: otio.opentime.TimeRange


@dataclass(frozen=True)
class SkippedMediaItem:
    path: Path
    folder_name: str
    reason: str


class ProgressReporter:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def report(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr, flush=True)


def rt(frames: float, fps: float) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(frames, fps)


def tr(
    start_frames: float,
    duration_frames: float,
    fps: float,
) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=rt(start_frames, fps),
        duration=rt(duration_frames, fps),
    )


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTENSIONS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def is_audio(path: Path) -> bool:
    return path.suffix.lower() in AUDIO_EXTENSIONS


def is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def run_json_command(command: list[str]) -> dict | list:
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def ffprobe_media_info(path: Path, ffprobe: str) -> dict:
    """
    Reads visual and audio stream metadata in one ffprobe call.
    """
    return run_json_command(
        [
            ffprobe,
            "-v", "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=index,codec_type,codec_name,width,height,duration,start_time,"
                "r_frame_rate,"
                "avg_frame_rate,time_base,sample_aspect_ratio,"
                "display_aspect_ratio,channels,channel_layout,sample_rate:"
                "stream_tags=rotate:"
                "stream_side_data=rotation"
            ),
            "-of", "json",
            str(path),
        ]
    )


def parse_ratio(value: str | None) -> Fraction | None:
    if not value or value in {"N/A", "0:1", "0/0"}:
        return None

    separator = ":" if ":" in value else "/"
    parts = value.split(separator, maxsplit=1)

    if len(parts) != 2:
        return None

    try:
        numerator = int(parts[0])
        denominator = int(parts[1])
    except ValueError:
        return None

    if numerator <= 0 or denominator <= 0:
        return None

    return Fraction(numerator, denominator)


def parse_frame_rate(value: str | None) -> Fraction | None:
    ratio = parse_ratio(value)
    if ratio is None or ratio <= 0:
        return None
    return ratio


def parse_optional_float(value: object) -> float | None:
    if value is None:
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(parsed):
        return None

    return parsed


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None

    if parsed < 0:
        return None

    return parsed


def rotation_from_stream(stream: dict) -> float | None:
    rotation = stream.get("tags", {}).get("rotate")

    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = side_data["rotation"]
            break

    return parse_optional_float(rotation)


def rotated_dimensions(
    width: int,
    height: int,
    rotation_degrees: float | None,
) -> tuple[int, int]:
    if rotation_degrees is None:
        return width, height

    if abs(round(rotation_degrees)) % 180 == 90:
        return height, width

    return width, height


COMMON_ASPECT_RATIOS = {
    "16:9": Fraction(16, 9),
    "9:16": Fraction(9, 16),
    "4:3": Fraction(4, 3),
    "3:4": Fraction(3, 4),
    "3:2": Fraction(3, 2),
    "2:3": Fraction(2, 3),
    "1:1": Fraction(1, 1),
}


def normalize_aspect_ratio(ratio: Fraction) -> str:
    ratio_value = float(ratio)

    for label, common_ratio in COMMON_ASPECT_RATIOS.items():
        common_value = float(common_ratio)
        relative_error = abs(ratio_value - common_value) / common_value

        if relative_error <= 0.02:
            return label

    exact = ratio.limit_denominator(100)
    return f"{exact.numerator}:{exact.denominator}"


def display_ratio_from_stream(
    stream: dict,
    raw_width: int,
    raw_height: int,
    rotation_degrees: float | None,
) -> Fraction:
    display_aspect_ratio = parse_ratio(stream.get("display_aspect_ratio"))
    if display_aspect_ratio is not None:
        if rotation_degrees is not None and abs(round(rotation_degrees)) % 180 == 90:
            return 1 / display_aspect_ratio
        return display_aspect_ratio

    sample_aspect_ratio = parse_ratio(stream.get("sample_aspect_ratio"))
    if sample_aspect_ratio is None:
        sample_aspect_ratio = Fraction(1, 1)

    if rotation_degrees is not None and abs(round(rotation_degrees)) % 180 == 90:
        return Fraction(raw_height, raw_width) / sample_aspect_ratio

    return Fraction(raw_width, raw_height) * sample_aspect_ratio


def streams_by_type(data: dict, codec_type: str) -> list[dict]:
    return [
        stream
        for stream in data.get("streams", [])
        if stream.get("codec_type") == codec_type
    ]


def first_visual_stream(data: dict, path: Path) -> dict:
    streams = streams_by_type(data, "video")
    if not streams:
        raise RuntimeError(f"No video/image stream found in: {path}")

    return streams[0]


def audio_streams(data: dict) -> list[dict]:
    return streams_by_type(data, "audio")


def exiftool_epoch_seconds(path: Path, exiftool: str | None) -> float | None:
    """
    Tries to read real capture/create time.
    Falls back to None when exiftool or metadata is unavailable.
    """
    if not exiftool:
        return None

    fields = [
        "-DateTimeOriginal",
        "-CreateDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-FileModifyDate",
    ]

    try:
        data = run_json_command(
            [
                exiftool,
                "-j",
                "-api", "QuickTimeUTC",
                "-d", "%s",
                *fields,
                str(path),
            ]
        )

        record = data[0]

        for key in [
            "DateTimeOriginal",
            "CreateDate",
            "MediaCreateDate",
            "TrackCreateDate",
            "FileModifyDate",
        ]:
            value = record.get(key)
            if value is not None:
                return float(value)

    except Exception:
        return None

    return None


def probe_sort_time(path: Path, exiftool: str | None) -> float:
    metadata_time = exiftool_epoch_seconds(path, exiftool)

    if metadata_time is not None:
        return metadata_time

    return path.stat().st_mtime


def discover_source_folders(input_root: Path) -> list[Path]:
    """
    Treat immediate child folders as source folders.
    If input_root has no child folders, treat input_root itself as the only folder.
    """
    child_folders = sorted(
        [p for p in input_root.iterdir() if p.is_dir()],
        key=lambda p: p.name.lower(),
    )

    return child_folders if child_folders else [input_root]


def collect_media_files(folder: Path, recursive: bool) -> list[Path]:
    candidates = folder.rglob("*") if recursive else folder.iterdir()

    files = [p for p in candidates if is_media(p)]

    return sorted(files, key=lambda p: str(p).lower())


def stream_or_format_duration_seconds(
    data: dict,
    stream: dict,
    media_description: str,
) -> float:
    stream_duration = parse_optional_float(stream.get("duration"))
    if stream_duration is not None and stream_duration > 0:
        return stream_duration

    format_duration = parse_optional_float(data.get("format", {}).get("duration"))
    if format_duration is not None and format_duration > 0:
        return format_duration

    raise RuntimeError(f"Could not determine {media_description} duration")


def native_video_frame_rate(stream: dict) -> tuple[float | None, str | None]:
    avg_frame_rate = parse_frame_rate(stream.get("avg_frame_rate"))
    if avg_frame_rate is not None:
        return float(avg_frame_rate), str(avg_frame_rate)

    r_frame_rate = parse_frame_rate(stream.get("r_frame_rate"))
    if r_frame_rate is not None:
        return float(r_frame_rate), str(r_frame_rate)

    return None, None


def audio_profile_from_stream(
    stream: dict,
) -> tuple[str, int | None, str | None, int | None]:
    channels = parse_optional_int(stream.get("channels"))
    channel_layout = stream.get("channel_layout")
    sample_rate = parse_optional_int(stream.get("sample_rate"))

    if channel_layout and channel_layout != "unknown":
        channel_label = channel_layout.replace("_", " ").title()
    elif channels == 1:
        channel_label = "Mono"
    elif channels == 2:
        channel_label = "Stereo"
    elif channels is not None:
        channel_label = f"{channels}ch"
    else:
        channel_label = "Unknown Channels"

    if sample_rate:
        if sample_rate % 1000 == 0:
            rate_label = f"{sample_rate // 1000}kHz"
        else:
            rate_label = f"{sample_rate}Hz"
    else:
        rate_label = "Unknown Rate"

    return f"{channel_label} {rate_label}", channels, channel_layout, sample_rate


def make_visual_media_item(
    path: Path,
    folder_name: str,
    data: dict,
    stream: dict,
    embedded_audio_streams: list[dict],
    sort_time: float,
    timeline_fps: float,
    image_duration_seconds: float,
) -> MediaItem:
    raw_width = int(stream["width"])
    raw_height = int(stream["height"])
    rotation_degrees = rotation_from_stream(stream)
    width, height = rotated_dimensions(raw_width, raw_height, rotation_degrees)
    display_ratio = display_ratio_from_stream(
        stream,
        raw_width,
        raw_height,
        rotation_degrees,
    )
    aspect_ratio = normalize_aspect_ratio(display_ratio)
    embedded_audio_profiles = [
        audio_profile_from_stream(audio_stream)[0]
        for audio_stream in embedded_audio_streams
    ]

    if is_image(path):
        media_type: MediaType = "Images"
        duration_seconds = image_duration_seconds
        duration_frames = max(1, round(duration_seconds * timeline_fps))
        source_fps = timeline_fps
        source_fps_label = str(timeline_fps)
        source_rate_units = "frames_per_second"
        native_rate = None
        native_rate_label = None
        source_duration_frames = duration_frames
        timing_policy = "still_external_reference_duration"
        available_range = tr(0, source_duration_frames, source_fps)
        source_range = available_range
    else:
        media_type = "Videos"
        duration_seconds = stream_or_format_duration_seconds(data, stream, "video")
        native_rate, native_rate_label = native_video_frame_rate(stream)
        source_fps = timeline_fps
        source_fps_label = str(timeline_fps)
        source_rate_units = "frames_per_second"
        timing_policy = "resolve_compatible_timeline_fps_video"
        source_start_seconds = parse_optional_float(stream.get("start_time")) or 0.0
        source_start_frames = round(source_start_seconds * source_fps)
        duration_frames = max(1, round(duration_seconds * timeline_fps))
        source_duration_frames = duration_frames
        available_range = tr(source_start_frames, source_duration_frames, source_fps)
        source_range = available_range

    return MediaItem(
        path=path,
        folder_name=folder_name,
        track_kind="Video",
        media_type=media_type,
        stream_role="visual",
        stream_index=parse_optional_int(stream.get("index")),
        grouping_label=aspect_ratio,
        raw_width=raw_width,
        raw_height=raw_height,
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        sample_aspect_ratio=stream.get("sample_aspect_ratio"),
        display_aspect_ratio=stream.get("display_aspect_ratio"),
        rotation_degrees=rotation_degrees,
        codec_name=stream.get("codec_name"),
        audio_profile=None,
        audio_channels=None,
        audio_channel_layout=None,
        audio_sample_rate=None,
        embedded_audio_stream_count=len(embedded_audio_streams),
        embedded_audio_profiles=embedded_audio_profiles,
        sort_time=sort_time,
        duration_frames=duration_frames,
        source_fps=source_fps,
        source_fps_label=source_fps_label,
        source_rate_units=source_rate_units,
        native_rate=native_rate,
        native_rate_label=native_rate_label,
        source_duration_seconds=duration_seconds,
        source_duration_frames=source_duration_frames,
        timing_policy=timing_policy,
        available_range=available_range,
        source_range=source_range,
    )


def make_audio_media_item(
    path: Path,
    folder_name: str,
    data: dict,
    stream: dict,
    sort_time: float,
    timeline_fps: float,
    stream_role: Literal["embedded_audio", "audio_only"],
    visual_duration_seconds: float | None = None,
    visual_duration_frames: int | None = None,
) -> MediaItem:
    audio_profile, channels, channel_layout, sample_rate = audio_profile_from_stream(
        stream
    )
    audio_duration_seconds = stream_or_format_duration_seconds(data, stream, "audio")
    source_fps = timeline_fps
    source_fps_label = str(timeline_fps)
    source_rate_units = "frames_per_second"
    native_rate = float(sample_rate) if sample_rate else None
    native_rate_label = str(sample_rate) if sample_rate else None
    timing_policy = "resolve_compatible_timeline_fps_audio"
    source_start_seconds = parse_optional_float(stream.get("start_time")) or 0.0
    source_start_frames = round(source_start_seconds * source_fps)

    if stream_role == "embedded_audio" and visual_duration_seconds is not None:
        duration_seconds = visual_duration_seconds
        duration_frames = (
            visual_duration_frames
            if visual_duration_frames is not None
            else max(1, round(duration_seconds * timeline_fps))
        )
    else:
        duration_seconds = audio_duration_seconds
        duration_frames = max(1, round(duration_seconds * timeline_fps))

    source_duration_frames = duration_frames
    available_range = tr(source_start_frames, source_duration_frames, source_fps)

    return MediaItem(
        path=path,
        folder_name=folder_name,
        track_kind="Audio",
        media_type="Audio",
        stream_role=stream_role,
        stream_index=parse_optional_int(stream.get("index")),
        grouping_label=audio_profile,
        raw_width=None,
        raw_height=None,
        width=None,
        height=None,
        aspect_ratio=None,
        sample_aspect_ratio=None,
        display_aspect_ratio=None,
        rotation_degrees=None,
        codec_name=stream.get("codec_name"),
        audio_profile=audio_profile,
        audio_channels=channels,
        audio_channel_layout=channel_layout,
        audio_sample_rate=sample_rate,
        embedded_audio_stream_count=0,
        embedded_audio_profiles=[],
        sort_time=sort_time,
        duration_frames=duration_frames,
        source_fps=source_fps,
        source_fps_label=source_fps_label,
        source_rate_units=source_rate_units,
        native_rate=native_rate,
        native_rate_label=native_rate_label,
        source_duration_seconds=duration_seconds,
        source_duration_frames=source_duration_frames,
        timing_policy=timing_policy,
        available_range=available_range,
        source_range=available_range,
    )


def probe_media_items(
    path: Path,
    folder_name: str,
    ffprobe: str,
    exiftool: str | None,
    timeline_fps: float,
    image_duration_seconds: float,
    audio_mode: AudioMode,
) -> list[MediaItem]:
    data = ffprobe_media_info(path, ffprobe)
    sort_time = probe_sort_time(path, exiftool)
    items: list[MediaItem] = []

    if is_image(path) or is_video(path):
        visual_stream = first_visual_stream(data, path)
        embedded_audio_streams = audio_streams(data) if is_video(path) else []
        visual_item = make_visual_media_item(
            path=path,
            folder_name=folder_name,
            data=data,
            stream=visual_stream,
            embedded_audio_streams=embedded_audio_streams,
            sort_time=sort_time,
            timeline_fps=timeline_fps,
            image_duration_seconds=image_duration_seconds,
        )
        items.append(visual_item)

        if is_video(path) and audio_mode == "split-embedded":
            for audio_stream in embedded_audio_streams:
                items.append(
                    make_audio_media_item(
                        path=path,
                        folder_name=folder_name,
                        data=data,
                        stream=audio_stream,
                        sort_time=sort_time,
                        timeline_fps=timeline_fps,
                        stream_role="embedded_audio",
                        visual_duration_seconds=visual_item.source_duration_seconds,
                        visual_duration_frames=visual_item.duration_frames,
                    )
                )
    elif is_audio(path):
        if audio_mode not in {"linked", "standalone", "split-embedded"}:
            return items

        streams = audio_streams(data)
        if not streams:
            raise RuntimeError(f"No audio stream found in: {path}")

        for stream in streams:
            items.append(
                make_audio_media_item(
                    path=path,
                    folder_name=folder_name,
                    data=data,
                    stream=stream,
                    sort_time=sort_time,
                    timeline_fps=timeline_fps,
                    stream_role="audio_only",
                )
            )
    else:
        raise RuntimeError(f"Unsupported media extension: {path.suffix}")

    return items


def build_media_items(
    input_root: Path,
    fps: float,
    image_duration_seconds: float,
    recursive: bool,
    strict: bool,
    audio_mode: AudioMode,
    progress: ProgressReporter,
) -> tuple[list[MediaItem], list[SkippedMediaItem]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError(
            "ffprobe not found. Install ffmpeg first: brew install ffmpeg"
        )

    exiftool = shutil.which("exiftool")
    progress.report(f"Discovering source folders under: {input_root}")
    source_folders = discover_source_folders(input_root)
    progress.report(f"Found {len(source_folders)} source folder(s).")

    items: list[MediaItem] = []
    skipped_items: list[SkippedMediaItem] = []
    media_by_folder: list[tuple[Path, list[Path]]] = []
    total_files = 0

    for folder in source_folders:
        media_files = collect_media_files(folder, recursive=recursive)
        media_by_folder.append((folder, media_files))
        total_files += len(media_files)
        progress.report(f"Collected {len(media_files)} media file(s) from {folder}.")

    processed_files = 0

    for folder, media_files in media_by_folder:
        for path in media_files:
            processed_files += 1
            progress.report(f"Probing {processed_files}/{total_files}: {path}")
            try:
                probed_items = probe_media_items(
                    path=path,
                    folder_name=folder.name,
                    ffprobe=ffprobe,
                    exiftool=exiftool,
                    timeline_fps=fps,
                    image_duration_seconds=image_duration_seconds,
                    audio_mode=audio_mode,
                )
                items.extend(probed_items)
                progress.report(
                    f"Added {len(probed_items)} timeline item(s) from {path.name}."
                )
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"Failed to probe media: {path}: {exc}") from exc

                skipped_items.append(
                    SkippedMediaItem(
                        path=path,
                        folder_name=folder.name,
                        reason=str(exc),
                    )
                )
                progress.report(f"Skipped {path}: {exc}")

    items = sorted(
        items,
        key=lambda item: (
            item.sort_time,
            item.folder_name.lower(),
            item.track_kind,
            item.media_type,
            item.grouping_label,
            item.stream_index if item.stream_index is not None else -1,
            item.path.name.lower(),
        ),
    )

    progress.report(
        f"Finished probing. Timeline items: {len(items)}. Skipped media: {len(skipped_items)}."
    )
    embedded_audio_video_count = sum(
        1
        for item in items
        if item.track_kind == "Video" and item.embedded_audio_stream_count > 0
    )
    if audio_mode != "split-embedded" and embedded_audio_video_count:
        progress.report(
            "Resolve-safe audio mode: "
            f"{embedded_audio_video_count} video file(s) have embedded audio and "
            "were referenced once instead of being split onto audio tracks."
        )
    return items, skipped_items


def make_gap(duration_frames: int, fps: float) -> otio.schema.Gap:
    return otio.schema.Gap(
        name="Gap",
        source_range=tr(0, duration_frames, fps),
    )


def make_clip(item: MediaItem, fps: float) -> otio.schema.Clip:
    media_reference = otio.schema.ExternalReference(
        target_url=item.path.resolve().as_uri(),
        available_range=item.available_range,
    )

    return otio.schema.Clip(
        name=item.path.name,
        media_reference=media_reference,
        source_range=item.source_range,
        metadata={
            "folder_to_otio": {
                "source_path": str(item.path.resolve()),
                "folder": item.folder_name,
                "track_kind": item.track_kind,
                "media_type": item.media_type,
                "stream_role": item.stream_role,
                "stream_index": item.stream_index,
                "grouping_label": item.grouping_label,
                "raw_width": item.raw_width,
                "raw_height": item.raw_height,
                "width": item.width,
                "height": item.height,
                "aspect_ratio": item.aspect_ratio,
                "sample_aspect_ratio": item.sample_aspect_ratio,
                "display_aspect_ratio": item.display_aspect_ratio,
                "rotation_degrees": item.rotation_degrees,
                "codec_name": item.codec_name,
                "audio_profile": item.audio_profile,
                "audio_channels": item.audio_channels,
                "audio_channel_layout": item.audio_channel_layout,
                "audio_sample_rate": item.audio_sample_rate,
                "embedded_audio_stream_count": item.embedded_audio_stream_count,
                "embedded_audio_profiles": item.embedded_audio_profiles,
                "sort_time": item.sort_time,
                "timeline_fps": fps,
                "timeline_duration_frames": item.duration_frames,
                "source_fps": item.source_fps,
                "source_fps_label": item.source_fps_label,
                "source_rate_units": item.source_rate_units,
                "native_rate": item.native_rate,
                "native_rate_label": item.native_rate_label,
                "source_duration_seconds": item.source_duration_seconds,
                "source_duration_frames": item.source_duration_frames,
                "timing_policy": item.timing_policy,
            }
        },
    )


def track_key(item: MediaItem) -> tuple[str, str, str, str]:
    return item.folder_name, item.track_kind, item.media_type, item.grouping_label


def make_track_name(
    folder_name: str,
    media_type: str,
    grouping_label: str,
) -> str:
    return f"{folder_name} - {media_type} - {grouping_label}"


def create_tracks(
    items: list[MediaItem],
) -> dict[tuple[str, str, str, str], otio.schema.Track]:
    keys = sorted(
        {track_key(item) for item in items},
        key=lambda key: (key[1] != "Video", key[0].lower(), key[2], key[3]),
    )

    tracks: dict[tuple[str, str, str, str], otio.schema.Track] = {}

    for folder_name, track_kind, media_type, grouping_label in keys:
        otio_track_kind = (
            otio.schema.TrackKind.Video
            if track_kind == "Video"
            else otio.schema.TrackKind.Audio
        )
        tracks[(folder_name, track_kind, media_type, grouping_label)] = otio.schema.Track(
            name=make_track_name(
                folder_name,
                media_type,
                grouping_label,
            ),
            kind=otio_track_kind,
        )

    return tracks


def build_global_sequence_tracks(
    items: list[MediaItem],
    fps: float,
) -> dict[tuple[str, str, str, str], otio.schema.Track]:
    """
    All media is ordered chronologically across all folders.
    Each item is placed on its own folder/type/aspect-ratio track.
    Gaps are inserted so the item lands at the correct timeline position.

    This avoids unwanted overlap and gives you one chronological assembly.
    """
    tracks = create_tracks(items)
    track_lengths: dict[tuple[str, str, str, str], int] = {
        key: 0 for key in tracks
    }

    global_cursor = 0

    grouped_items: dict[tuple[float, str], list[MediaItem]] = {}
    for item in items:
        event_key = (item.sort_time, str(item.path.resolve()))
        grouped_items.setdefault(event_key, []).append(item)

    for event_key in sorted(grouped_items, key=lambda key: (key[0], key[1].lower())):
        event_items = sorted(
            grouped_items[event_key],
            key=lambda item: (
                item.track_kind != "Video",
                item.media_type,
                item.grouping_label,
                item.stream_index if item.stream_index is not None else -1,
            ),
        )
        event_duration = max(item.duration_frames for item in event_items)

        for item in event_items:
            key = track_key(item)
            track = tracks[key]

            current_track_length = track_lengths[key]

            if current_track_length < global_cursor:
                track.append(make_gap(global_cursor - current_track_length, fps))

            track.append(make_clip(item, fps))

            track_lengths[key] = global_cursor + item.duration_frames

        global_cursor += event_duration

    # Pad all tracks to the same final length.
    for key, track in tracks.items():
        current_track_length = track_lengths[key]

        if current_track_length < global_cursor:
            track.append(make_gap(global_cursor - current_track_length, fps))

    return tracks


def build_per_track_sequence_tracks(
    items: list[MediaItem],
    fps: float,
) -> dict[tuple[str, str, str, str], otio.schema.Track]:
    """
    Each folder/type/aspect-ratio track is ordered independently and starts at
    00:00:00:00.
    This is useful if you want separate organized lanes for manual editing,
    but clips from different tracks will overlap in time.
    """
    tracks = create_tracks(items)

    grouped: dict[tuple[str, str, str, str], list[MediaItem]] = {
        key: [] for key in tracks
    }

    for item in items:
        grouped[track_key(item)].append(item)

    max_length = 0
    track_lengths: dict[tuple[str, str, str, str], int] = {}

    for key, group_items in grouped.items():
        track = tracks[key]
        length = 0

        for item in sorted(
            group_items,
            key=lambda i: (
                i.sort_time,
                i.stream_index if i.stream_index is not None else -1,
                i.path.name.lower(),
            ),
        ):
            track.append(make_clip(item, fps))
            length += item.duration_frames

        track_lengths[key] = length
        max_length = max(max_length, length)

    # Pad all tracks to the same final length.
    for key, track in tracks.items():
        current_length = track_lengths[key]

        if current_length < max_length:
            track.append(make_gap(max_length - current_length, fps))

    return tracks


def build_timeline(
    items: list[MediaItem],
    skipped_items: list[SkippedMediaItem],
    timeline_name: str,
    fps: float,
    width: int,
    height: int,
    image_duration_seconds: float,
    layout_mode: LayoutMode,
    audio_mode: AudioMode,
    progress: ProgressReporter,
) -> otio.schema.Timeline:
    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.global_start_time = rt(0, fps)

    timeline.metadata["folder_to_otio"] = {
        "timeline_fps": fps,
        "target_width": width,
        "target_height": height,
        "image_duration_seconds": image_duration_seconds,
        "layout_mode": layout_mode,
        "audio_mode": audio_mode,
        "embedded_audio_policy": (
            "video files are referenced once; embedded audio is left in the source container"
            if audio_mode != "split-embedded"
            else "embedded audio is split into separate OTIO audio clips"
        ),
        "skipped_media": [
            {
                "source_path": str(item.path.resolve()),
                "folder": item.folder_name,
                "reason": item.reason,
            }
            for item in skipped_items
        ],
        "note": (
            "Resolution is metadata only. Set 3840x2160 / 24 fps manually "
            "in DaVinci Resolve during or before OTIO import."
        ),
    }

    if layout_mode == "global-sequence":
        progress.report("Building global sequence tracks.")
        tracks = build_global_sequence_tracks(items, fps)
    elif layout_mode == "per-track-sequence":
        progress.report("Building per-track sequence tracks.")
        tracks = build_per_track_sequence_tracks(items, fps)
    else:
        raise ValueError(f"Unsupported layout mode: {layout_mode}")

    progress.report(f"Created {len(tracks)} track(s).")

    for key in sorted(tracks, key=lambda k: (k[1] != "Video", k[0].lower(), k[2], k[3])):
        timeline.tracks.append(tracks[key])

    return timeline


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate one OTIO timeline from folders of image, video, and audio media."
    )

    parser.add_argument(
        "input_root",
        type=Path,
        help="Root folder containing your source folders.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated_timeline.otio"),
        help="Output .otio file.",
    )

    parser.add_argument(
        "--timeline-name",
        default=DEFAULT_TIMELINE_NAME,
        help="Timeline name inside the OTIO file.",
    )

    parser.add_argument(
        "--fps",
        type=positive_float,
        default=DEFAULT_FPS,
        help="Timeline timing rate. Default: 24.",
    )

    parser.add_argument(
        "--width",
        type=positive_int,
        default=DEFAULT_WIDTH,
        help="Target timeline width metadata. Default: 3840.",
    )

    parser.add_argument(
        "--height",
        type=positive_int,
        default=DEFAULT_HEIGHT,
        help="Target timeline height metadata. Default: 2160.",
    )

    parser.add_argument(
        "--image-duration",
        type=positive_float,
        default=DEFAULT_IMAGE_DURATION_SECONDS,
        help="Still image duration in seconds. Default: 3.",
    )

    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Search media recursively inside each source folder.",
    )

    parser.add_argument(
        "--layout-mode",
        choices=["global-sequence", "per-track-sequence"],
        default="global-sequence",
        help=(
            "global-sequence = one chronological assembly with gaps. "
            "per-track-sequence = each track starts at 0 independently."
        ),
    )

    parser.add_argument(
        "--audio-mode",
        choices=["linked", "standalone", "none", "split-embedded"],
        default="linked",
        help=(
            "Audio import mode. linked = Resolve-safe default; video files are "
            "referenced once and standalone audio files are imported. "
            "standalone = video/images plus standalone audio only. "
            "none = video/images only. split-embedded = experimental; creates "
            "separate audio clips for embedded video audio."
        ),
    )

    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on the first media file that cannot be probed.",
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress output. Final summary and errors are still printed.",
    )

    args = parser.parse_args()
    progress = ProgressReporter(quiet=args.quiet)

    input_root = args.input_root.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not input_root.exists():
        raise FileNotFoundError(f"Input root does not exist: {input_root}")

    if output.suffix.lower() != ".otio":
        raise ValueError("Output file should end with .otio")

    items, skipped_items = build_media_items(
        input_root=input_root,
        fps=args.fps,
        image_duration_seconds=args.image_duration,
        recursive=args.recursive,
        strict=args.strict,
        audio_mode=args.audio_mode,
        progress=progress,
    )

    if not items:
        skipped_note = (
            f" Skipped {len(skipped_items)} media file(s)."
            if skipped_items else ""
        )
        raise RuntimeError(
            f"No supported media found under: {input_root}.{skipped_note}"
        )

    timeline = build_timeline(
        items=items,
        skipped_items=skipped_items,
        timeline_name=args.timeline_name,
        fps=args.fps,
        width=args.width,
        height=args.height,
        image_duration_seconds=args.image_duration,
        layout_mode=args.layout_mode,
        audio_mode=args.audio_mode,
        progress=progress,
    )

    output.parent.mkdir(parents=True, exist_ok=True)

    progress.report(f"Writing OTIO file: {output}")
    otio.adapters.write_to_file(timeline, str(output))
    progress.report("Finished writing OTIO file.")

    print(f"Generated: {output}")
    print(f"Timeline: {args.timeline_name}")
    print(f"Items: {len(items)}")
    print(f"Video items: {sum(1 for item in items if item.track_kind == 'Video')}")
    print(f"Audio items: {sum(1 for item in items if item.track_kind == 'Audio')}")
    print(f"Tracks: {len(timeline.tracks)}")
    print(f"FPS: {args.fps}")
    print(f"Target resolution metadata: {args.width}x{args.height}")
    print(f"Image duration: {args.image_duration}s")
    print(f"Layout mode: {args.layout_mode}")
    print(f"Audio mode: {args.audio_mode}")
    print(
        "Video files with embedded audio: "
        f"{sum(1 for item in items if item.track_kind == 'Video' and item.embedded_audio_stream_count > 0)}"
    )
    print(f"Skipped media: {len(skipped_items)}")

    for skipped_item in skipped_items:
        print(f"Skipped: {skipped_item.path} ({skipped_item.reason})")


if __name__ == "__main__":
    main()
