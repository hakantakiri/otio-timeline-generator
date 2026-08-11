from __future__ import annotations

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


DEFAULT_TIMELINE_NAME = "Generated Resolve Timeline"
DEFAULT_FPS = 24.0
DEFAULT_IMAGE_DURATION_SECONDS = 3.0

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts"}
AUDIO_EXTENSIONS = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

COMMON_ASPECT_RATIOS = {
    "16:9": Fraction(16, 9),
    "9:16": Fraction(9, 16),
    "4:3": Fraction(4, 3),
    "3:4": Fraction(3, 4),
    "3:2": Fraction(3, 2),
    "2:3": Fraction(2, 3),
    "1:1": Fraction(1, 1),
}

AudioMode = Literal["linked", "standalone", "none"]
FileUrlMode = Literal["resolve", "encoded"]
LayoutMode = Literal["global-sequence", "per-track-sequence"]
ResolveLayout = Literal["organized", "linked-pairs"]
MediaKind = Literal["image", "video", "audio"]
ClipRole = Literal["visual", "linked_audio", "standalone_audio"]

MEDIA_MANIFEST_SCHEMA = "media_manifest_v1"
VALIDATION_REPORT_SCHEMA = "validation_report_v1"
TRACK_PLAN_SCHEMA = "track_plan_v1"


@dataclass(frozen=True)
class AudioInfo:
    profile: str
    channels: int | None
    channel_layout: str | None
    sample_rate: int | None
    codec_name: str | None
    stream_index: int | None


@dataclass(frozen=True)
class SourceItem:
    path: Path
    folder_name: str
    media_kind: MediaKind
    sort_time: float
    duration_seconds: float
    duration_frames: int
    raw_width: int | None = None
    raw_height: int | None = None
    width: int | None = None
    height: int | None = None
    aspect_ratio: str | None = None
    codec_name: str | None = None
    has_audio: bool = False
    primary_audio: AudioInfo | None = None
    embedded_audio_profiles: tuple[str, ...] = ()

    def range(self, fps: float) -> otio.opentime.TimeRange:
        return time_range(self.duration_frames, fps)


@dataclass(frozen=True)
class SkippedMedia:
    path: Path
    folder_name: str
    reason: str


@dataclass(frozen=True)
class StemCollision:
    stem_key: str
    items: tuple[SourceItem, ...]


@dataclass(frozen=True)
class TimelineClip:
    item: SourceItem
    role: ClipRole
    track_key: tuple[str, str, str]
    link_group_id: int | None = None

    @property
    def duration_frames(self) -> int:
        return self.item.duration_frames

    @property
    def sort_time(self) -> float:
        return self.item.sort_time


class ProgressReporter:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet

    def report(self, message: str) -> None:
        if not self.quiet:
            print(message, file=sys.stderr, flush=True)


def rational_time(frames: float, fps: float) -> otio.opentime.RationalTime:
    return otio.opentime.RationalTime(frames, fps)


def time_range(frames: int, fps: float) -> otio.opentime.TimeRange:
    return otio.opentime.TimeRange(
        start_time=rational_time(0, fps),
        duration=rational_time(frames, fps),
    )


def is_media(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS


def media_kind(path: Path) -> MediaKind:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    raise ValueError(f"Unsupported media extension: {path.suffix}")


def run_json_command(command: list[str]) -> dict | list:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def ffprobe_media_info(path: Path, ffprobe: str) -> dict:
    return run_json_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration:"
                "stream=index,codec_type,codec_name,width,height,duration,start_time,"
                "avg_frame_rate,r_frame_rate,sample_aspect_ratio,display_aspect_ratio,"
                "channels,channel_layout,sample_rate:"
                "stream_tags=rotate:"
                "stream_side_data=rotation"
            ),
            "-of",
            "json",
            str(path),
        ]
    )


def parse_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


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


def normalize_aspect_ratio(ratio: Fraction) -> str:
    ratio_value = float(ratio)
    for label, common_ratio in COMMON_ASPECT_RATIOS.items():
        relative_error = abs(ratio_value - float(common_ratio)) / float(common_ratio)
        if relative_error <= 0.02:
            return label

    exact = ratio.limit_denominator(100)
    return f"{exact.numerator}:{exact.denominator}"


def stream_rotation(stream: dict) -> float | None:
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
    if rotation_degrees is not None and abs(round(rotation_degrees)) % 180 == 90:
        return height, width
    return width, height


def display_ratio(
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

    sample_aspect_ratio = parse_ratio(stream.get("sample_aspect_ratio")) or Fraction(1, 1)
    if rotation_degrees is not None and abs(round(rotation_degrees)) % 180 == 90:
        return Fraction(raw_height, raw_width) / sample_aspect_ratio
    return Fraction(raw_width, raw_height) * sample_aspect_ratio


def streams_by_type(data: dict, codec_type: str) -> list[dict]:
    return [
        stream
        for stream in data.get("streams", [])
        if stream.get("codec_type") == codec_type
    ]


def duration_seconds(data: dict, stream: dict | None, label: str) -> float:
    candidates: list[object] = []
    if stream is not None:
        candidates.extend([stream.get("duration"), stream.get("tags", {}).get("DURATION")])
    candidates.append(data.get("format", {}).get("duration"))

    for value in candidates:
        parsed = parse_optional_float(value)
        if parsed is not None and parsed > 0:
            return parsed

    raise RuntimeError(f"No usable {label} duration found")


def audio_info_from_stream(stream: dict) -> AudioInfo:
    channels = parse_optional_int(stream.get("channels"))
    channel_layout = stream.get("channel_layout")
    sample_rate = parse_optional_int(stream.get("sample_rate"))

    if channel_layout and channel_layout != "unknown":
        layout_label = channel_layout.replace("_", " ").title()
    elif channels is not None:
        layout_label = f"{channels}ch"
    else:
        layout_label = "Unknown Audio"

    if sample_rate is not None:
        if sample_rate % 1000 == 0:
            rate_label = f"{sample_rate // 1000}kHz"
        else:
            rate_label = f"{sample_rate}Hz"
        profile = f"{layout_label} {rate_label}"
    else:
        profile = layout_label

    return AudioInfo(
        profile=profile,
        channels=channels,
        channel_layout=channel_layout,
        sample_rate=sample_rate,
        codec_name=stream.get("codec_name"),
        stream_index=parse_optional_int(stream.get("index")),
    )


def exiftool_epoch_seconds(path: Path, exiftool: str | None) -> float | None:
    if not exiftool:
        return None

    try:
        result = subprocess.run(
            [
                exiftool,
                "-json",
                "-api",
                "QuickTimeUTC=1",
                "-d",
                "%s",
                "-DateTimeOriginal",
                "-CreateDate",
                "-MediaCreateDate",
                "-TrackCreateDate",
                "-FileModifyDate",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        records = json.loads(result.stdout)
        if not records:
            return None
        record = records[0]
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


def sort_time_for_path(path: Path, exiftool: str | None) -> float:
    metadata_time = exiftool_epoch_seconds(path, exiftool)
    return metadata_time if metadata_time is not None else path.stat().st_mtime


def discover_source_folders(input_root: Path) -> list[Path]:
    child_folders = sorted(
        [path for path in input_root.iterdir() if path.is_dir()],
        key=lambda path: path.name.lower(),
    )
    return child_folders if child_folders else [input_root]


def collect_media_files(folder: Path, recursive: bool) -> list[Path]:
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    return sorted([path for path in candidates if is_media(path)], key=lambda path: str(path).lower())


def source_item_from_path(
    path: Path,
    folder_name: str,
    ffprobe: str,
    exiftool: str | None,
    fps: float,
    image_duration_seconds: float,
) -> SourceItem:
    kind = media_kind(path)
    data = ffprobe_media_info(path, ffprobe)
    sort_time = sort_time_for_path(path, exiftool)
    video_streams = streams_by_type(data, "video")
    audio_streams = streams_by_type(data, "audio")

    if kind == "image":
        if not video_streams:
            raise RuntimeError("No image stream found")
        stream = video_streams[0]
        raw_width = int(stream["width"])
        raw_height = int(stream["height"])
        rotation_degrees = stream_rotation(stream)
        width, height = rotated_dimensions(raw_width, raw_height, rotation_degrees)
        frames = max(1, round(image_duration_seconds * fps))
        return SourceItem(
            path=path,
            folder_name=folder_name,
            media_kind=kind,
            sort_time=sort_time,
            duration_seconds=image_duration_seconds,
            duration_frames=frames,
            raw_width=raw_width,
            raw_height=raw_height,
            width=width,
            height=height,
            aspect_ratio=normalize_aspect_ratio(
                display_ratio(stream, raw_width, raw_height, rotation_degrees)
            ),
            codec_name=stream.get("codec_name"),
        )

    if kind == "video":
        if not video_streams:
            raise RuntimeError("No video stream found")
        stream = video_streams[0]
        raw_width = int(stream["width"])
        raw_height = int(stream["height"])
        rotation_degrees = stream_rotation(stream)
        width, height = rotated_dimensions(raw_width, raw_height, rotation_degrees)
        seconds = duration_seconds(data, stream, "video")
        frames = max(1, math.ceil(seconds * fps))
        audio_infos = tuple(audio_info_from_stream(stream) for stream in audio_streams)
        return SourceItem(
            path=path,
            folder_name=folder_name,
            media_kind=kind,
            sort_time=sort_time,
            duration_seconds=seconds,
            duration_frames=frames,
            raw_width=raw_width,
            raw_height=raw_height,
            width=width,
            height=height,
            aspect_ratio=normalize_aspect_ratio(
                display_ratio(stream, raw_width, raw_height, rotation_degrees)
            ),
            codec_name=stream.get("codec_name"),
            has_audio=bool(audio_infos),
            primary_audio=audio_infos[0] if audio_infos else None,
            embedded_audio_profiles=tuple(info.profile for info in audio_infos),
        )

    if kind == "audio":
        if not audio_streams:
            raise RuntimeError("No audio stream found")
        stream = audio_streams[0]
        seconds = duration_seconds(data, stream, "audio")
        frames = max(1, math.ceil(seconds * fps))
        audio_info = audio_info_from_stream(stream)
        return SourceItem(
            path=path,
            folder_name=folder_name,
            media_kind=kind,
            sort_time=sort_time,
            duration_seconds=seconds,
            duration_frames=frames,
            codec_name=audio_info.codec_name,
            has_audio=True,
            primary_audio=audio_info,
        )

    raise RuntimeError(f"Unsupported media kind: {kind}")


def build_source_items(
    input_root: Path,
    fps: float,
    image_duration_seconds: float,
    recursive: bool,
    strict: bool,
    progress: ProgressReporter,
) -> tuple[list[SourceItem], list[SkippedMedia], list[Path]]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe not found. Install FFmpeg before running this script.")

    exiftool = shutil.which("exiftool")
    source_folders = discover_source_folders(input_root)
    progress.report(f"Discovered {len(source_folders)} source folder(s).")

    media_by_folder: list[tuple[Path, list[Path]]] = []
    total_files = 0
    for folder in source_folders:
        media_files = collect_media_files(folder, recursive=recursive)
        media_by_folder.append((folder, media_files))
        total_files += len(media_files)
        progress.report(f"Collected {len(media_files)} media file(s) from {folder}.")

    items: list[SourceItem] = []
    skipped: list[SkippedMedia] = []
    processed_files = 0

    for folder, media_files in media_by_folder:
        for path in media_files:
            processed_files += 1
            progress.report(f"Probing {processed_files}/{total_files}: {path}")
            try:
                items.append(
                    source_item_from_path(
                        path=path,
                        folder_name=folder.name,
                        ffprobe=ffprobe,
                        exiftool=exiftool,
                        fps=fps,
                        image_duration_seconds=image_duration_seconds,
                    )
                )
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"Failed to probe media: {path}: {exc}") from exc
                skipped.append(SkippedMedia(path=path, folder_name=folder.name, reason=str(exc)))
                progress.report(f"Skipped {path}: {exc}")

    items.sort(
        key=lambda item: (
            item.sort_time,
            item.folder_name.lower(),
            item.media_kind,
            item.aspect_ratio or "",
            item.path.name.lower(),
        )
    )
    progress.report(f"Finished probing. Source items: {len(items)}. Skipped media: {len(skipped)}.")
    return items, skipped, source_folders


def audio_info_to_dict(info: AudioInfo | None) -> dict | None:
    if info is None:
        return None
    return {
        "profile": info.profile,
        "channels": info.channels,
        "channel_layout": info.channel_layout,
        "sample_rate": info.sample_rate,
        "codec_name": info.codec_name,
        "stream_index": info.stream_index,
    }


def audio_info_from_dict(data: dict | None) -> AudioInfo | None:
    if data is None:
        return None
    return AudioInfo(
        profile=data["profile"],
        channels=data.get("channels"),
        channel_layout=data.get("channel_layout"),
        sample_rate=data.get("sample_rate"),
        codec_name=data.get("codec_name"),
        stream_index=data.get("stream_index"),
    )


def source_item_to_dict(item: SourceItem) -> dict:
    return {
        "path": str(item.path.resolve()),
        "folder_name": item.folder_name,
        "media_kind": item.media_kind,
        "sort_time": item.sort_time,
        "duration_seconds": item.duration_seconds,
        "duration_frames": item.duration_frames,
        "raw_width": item.raw_width,
        "raw_height": item.raw_height,
        "width": item.width,
        "height": item.height,
        "aspect_ratio": item.aspect_ratio,
        "codec_name": item.codec_name,
        "has_audio": item.has_audio,
        "primary_audio": audio_info_to_dict(item.primary_audio),
        "embedded_audio_profiles": list(item.embedded_audio_profiles),
    }


def source_item_from_dict(data: dict) -> SourceItem:
    return SourceItem(
        path=Path(data["path"]),
        folder_name=data["folder_name"],
        media_kind=data["media_kind"],
        sort_time=float(data["sort_time"]),
        duration_seconds=float(data["duration_seconds"]),
        duration_frames=int(data["duration_frames"]),
        raw_width=data.get("raw_width"),
        raw_height=data.get("raw_height"),
        width=data.get("width"),
        height=data.get("height"),
        aspect_ratio=data.get("aspect_ratio"),
        codec_name=data.get("codec_name"),
        has_audio=bool(data.get("has_audio")),
        primary_audio=audio_info_from_dict(data.get("primary_audio")),
        embedded_audio_profiles=tuple(data.get("embedded_audio_profiles", [])),
    )


def skipped_media_to_dict(item: SkippedMedia) -> dict:
    return {
        "path": str(item.path.resolve()),
        "folder_name": item.folder_name,
        "reason": item.reason,
    }


def skipped_media_from_dict(data: dict) -> SkippedMedia:
    return SkippedMedia(
        path=Path(data["path"]),
        folder_name=data["folder_name"],
        reason=data["reason"],
    )


def manifest_from_scan(
    input_root: Path,
    source_folders: list[Path],
    items: list[SourceItem],
    skipped: list[SkippedMedia],
    fps: float,
    image_duration_seconds: float,
    recursive: bool,
    strict: bool,
) -> dict:
    return {
        "schema_version": MEDIA_MANIFEST_SCHEMA,
        "settings": {
            "input_root": str(input_root.resolve()),
            "fps": fps,
            "image_duration_seconds": image_duration_seconds,
            "recursive": recursive,
            "strict": strict,
        },
        "source_folders": [str(path.resolve()) for path in source_folders],
        "items": [source_item_to_dict(item) for item in items],
        "skipped": [skipped_media_to_dict(item) for item in skipped],
        "summary": {
            "source_folder_count": len(source_folders),
            "item_count": len(items),
            "skipped_count": len(skipped),
            "image_count": sum(1 for item in items if item.media_kind == "image"),
            "video_count": sum(1 for item in items if item.media_kind == "video"),
            "audio_count": sum(1 for item in items if item.media_kind == "audio"),
        },
    }


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_manifest(path: Path) -> tuple[dict, list[SourceItem], list[SkippedMedia]]:
    manifest = read_json(path)
    if manifest.get("schema_version") != MEDIA_MANIFEST_SCHEMA:
        raise ValueError(f"Expected {MEDIA_MANIFEST_SCHEMA} in {path}")
    items = [source_item_from_dict(item) for item in manifest.get("items", [])]
    skipped = [skipped_media_from_dict(item) for item in manifest.get("skipped", [])]
    return manifest, items, skipped


def resolve_collision_role(item: SourceItem) -> str:
    if item.media_kind == "image":
        return "still"
    if item.media_kind == "video":
        return "video"
    return "audio"


def proposed_collision_path(item: SourceItem) -> Path:
    role = resolve_collision_role(item)
    return item.path.with_name(f"{item.path.stem}__{role}{item.path.suffix}")


def find_stem_collisions(items: list[SourceItem]) -> list[StemCollision]:
    by_folder_and_stem: dict[tuple[Path, str], list[SourceItem]] = {}

    for item in items:
        key = (item.path.parent.resolve(), item.path.stem.lower())
        by_folder_and_stem.setdefault(key, []).append(item)

    collisions = [
        StemCollision(stem_key=stem_key, items=tuple(sorted(group, key=lambda i: i.path.name.lower())))
        for (_, stem_key), group in by_folder_and_stem.items()
        if len({item.path.suffix.lower() for item in group}) > 1
    ]

    return sorted(
        collisions,
        key=lambda collision: str(collision.items[0].path.parent / collision.stem_key).lower(),
    )


def validation_report_for_items(items: list[SourceItem]) -> dict:
    collisions = find_stem_collisions(items)
    return {
        "schema_version": VALIDATION_REPORT_SCHEMA,
        "ok": not collisions,
        "collision_count": len(collisions),
        "collisions": [
            {
                "stem": collision.items[0].path.stem,
                "folder": str(collision.items[0].path.parent.resolve()),
                "items": [
                    {
                        "path": str(item.path.resolve()),
                        "media_kind": item.media_kind,
                        "proposed_path": str(proposed_collision_path(item).resolve()),
                    }
                    for item in collision.items
                ],
            }
            for collision in collisions
        ],
    }


def visual_track_key(item: SourceItem) -> tuple[str, str, str]:
    media_label = "Images" if item.media_kind == "image" else "Videos"
    return item.folder_name, media_label, item.aspect_ratio or "Unknown"


def audio_track_key(item: SourceItem) -> tuple[str, str, str]:
    profile = item.primary_audio.profile if item.primary_audio else "Unknown Audio"
    return item.folder_name, "Audio", profile


def linked_audio_track_key(
    item: SourceItem,
    resolve_layout: ResolveLayout,
) -> tuple[str, str, str]:
    if resolve_layout == "linked-pairs":
        profile = item.primary_audio.profile if item.primary_audio else "Unknown Audio"
        aspect_ratio = item.aspect_ratio or "Unknown"
        return item.folder_name, "Linked Audio", f"{aspect_ratio} - {profile}"
    return audio_track_key(item)


def build_timeline_clips(
    items: list[SourceItem],
    audio_mode: AudioMode,
    resolve_layout: ResolveLayout,
    resolve_link_start_id: int,
) -> list[TimelineClip]:
    clips: list[TimelineClip] = []
    next_link_group_id = resolve_link_start_id

    for item in items:
        if item.media_kind in {"image", "video"}:
            link_group_id = None
            if audio_mode == "linked" and item.media_kind == "video" and item.has_audio:
                link_group_id = next_link_group_id
                next_link_group_id += 1

            clips.append(
                TimelineClip(
                    item=item,
                    role="visual",
                    track_key=visual_track_key(item),
                    link_group_id=link_group_id,
                )
            )

            if link_group_id is not None:
                clips.append(
                    TimelineClip(
                        item=item,
                        role="linked_audio",
                        track_key=linked_audio_track_key(item, resolve_layout),
                        link_group_id=link_group_id,
                    )
                )
        elif item.media_kind == "audio" and audio_mode in {"linked", "standalone"}:
            clips.append(
                TimelineClip(
                    item=item,
                    role="standalone_audio",
                    track_key=audio_track_key(item),
                )
            )

    return clips


def track_name_from_key(key: tuple[str, str, str]) -> str:
    folder_name, media_label, grouping_label = key
    return f"{folder_name} - {media_label} - {grouping_label}"


def clip_name(clip: TimelineClip) -> str:
    item = clip.item
    suffix = item.path.suffix
    stem = item.path.stem

    if clip.role == "visual":
        if item.media_kind == "image":
            role_label = "still"
        elif item.media_kind == "video":
            role_label = "video"
        else:
            role_label = item.media_kind
    elif clip.role == "linked_audio":
        role_label = "linked-audio"
    else:
        role_label = "audio"

    return f"{stem}__{role_label}{suffix}"


def track_plan_from_items(
    manifest: dict,
    items: list[SourceItem],
    layout_mode: LayoutMode,
    audio_mode: AudioMode,
    resolve_layout: ResolveLayout,
    resolve_link_start_id: int,
) -> dict:
    clips = build_timeline_clips(items, audio_mode, resolve_layout, resolve_link_start_id)
    tracks: dict[tuple[str, str, str], dict] = {}
    for clip in clips:
        tracks.setdefault(
            clip.track_key,
            {
                "track_key": list(clip.track_key),
                "name": track_name_from_key(clip.track_key),
                "kind": "Video" if clip.role == "visual" else "Audio",
                "clip_count": 0,
                "duration_frames": 0,
            },
        )
        tracks[clip.track_key]["clip_count"] += 1
        tracks[clip.track_key]["duration_frames"] += clip.duration_frames

    sorted_tracks = sorted(
        tracks.values(),
        key=lambda track: (
            track["kind"] != "Video",
            track["track_key"][0].lower(),
            track["track_key"][1],
            track["track_key"][2],
        ),
    )

    return {
        "schema_version": TRACK_PLAN_SCHEMA,
        "settings": {
            "fps": manifest["settings"]["fps"],
            "image_duration_seconds": manifest["settings"]["image_duration_seconds"],
            "layout_mode": layout_mode,
            "audio_mode": audio_mode,
            "resolve_layout": resolve_layout,
            "resolve_link_start_id": resolve_link_start_id,
        },
        "tracks": sorted_tracks,
        "clips": [
            {
                "source_path": str(clip.item.path.resolve()),
                "role": clip.role,
                "track_key": list(clip.track_key),
                "track_name": track_name_from_key(clip.track_key),
                "clip_name": clip_name(clip),
                "duration_frames": clip.duration_frames,
                "sort_time": clip.sort_time,
                "link_group_id": clip.link_group_id,
            }
            for clip in clips
        ],
        "summary": {
            "source_item_count": len(items),
            "clip_count": len(clips),
            "visual_clip_count": sum(1 for clip in clips if clip.role == "visual"),
            "linked_audio_clip_count": sum(1 for clip in clips if clip.role == "linked_audio"),
            "standalone_audio_clip_count": sum(1 for clip in clips if clip.role == "standalone_audio"),
            "track_count": len(sorted_tracks),
        },
    }


def load_track_plan(path: Path) -> dict:
    plan = read_json(path)
    if plan.get("schema_version") != TRACK_PLAN_SCHEMA:
        raise ValueError(f"Expected {TRACK_PLAN_SCHEMA} in {path}")
    return plan


def clips_from_plan(items: list[SourceItem], plan: dict) -> list[TimelineClip]:
    items_by_path = {str(item.path.resolve()): item for item in items}
    clips: list[TimelineClip] = []

    for clip_data in plan.get("clips", []):
        source_path = clip_data["source_path"]
        item = items_by_path.get(source_path)
        if item is None:
            raise ValueError(f"Track plan references media absent from manifest: {source_path}")
        clips.append(
            TimelineClip(
                item=item,
                role=clip_data["role"],
                track_key=tuple(clip_data["track_key"]),
                link_group_id=clip_data.get("link_group_id"),
            )
        )

    return clips


def file_url_for_path(path: Path, mode: FileUrlMode) -> str:
    resolved = path.resolve()
    if mode == "resolve":
        return "file://" + str(resolved)
    if mode == "encoded":
        return resolved.as_uri()
    raise ValueError(f"Unsupported file URL mode: {mode}")


def make_gap(duration_frames: int, fps: float) -> otio.schema.Gap:
    return otio.schema.Gap(name="Gap", source_range=time_range(duration_frames, fps))


def create_tracks(clips: list[TimelineClip]) -> dict[tuple[str, str, str], otio.schema.Track]:
    track_roles = {clip.track_key: clip.role for clip in clips}
    ordered_keys = sorted(
        track_roles,
        key=lambda key: (
            track_roles[key] != "visual",
            key[0].lower(),
            key[1],
            key[2],
        ),
    )

    return {
        key: otio.schema.Track(
            name=track_name_from_key(key),
            kind=otio.schema.TrackKind.Video
            if track_roles[key] == "visual"
            else otio.schema.TrackKind.Audio,
        )
        for key in ordered_keys
    }


def clip_metadata(clip: TimelineClip, fps: float, file_url_mode: FileUrlMode) -> dict:
    item = clip.item
    metadata = {
        "folder_to_otio": {
            "source_path": str(item.path.resolve()),
            "folder": item.folder_name,
            "media_kind": item.media_kind,
            "clip_role": clip.role,
            "grouping_label": clip.track_key[2],
            "raw_width": item.raw_width,
            "raw_height": item.raw_height,
            "width": item.width,
            "height": item.height,
            "aspect_ratio": item.aspect_ratio,
            "codec_name": item.codec_name,
            "has_audio": item.has_audio,
            "audio_profile": item.primary_audio.profile if item.primary_audio else None,
            "audio_channels": item.primary_audio.channels if item.primary_audio else None,
            "audio_channel_layout": item.primary_audio.channel_layout if item.primary_audio else None,
            "audio_sample_rate": item.primary_audio.sample_rate if item.primary_audio else None,
            "embedded_audio_profiles": list(item.embedded_audio_profiles),
            "sort_time": item.sort_time,
            "source_duration_seconds": item.duration_seconds,
            "timeline_duration_frames": item.duration_frames,
            "timeline_fps": fps,
            "file_url_mode": file_url_mode,
            "resolve_link_group_id": clip.link_group_id,
        }
    }

    if clip.link_group_id is not None:
        metadata["Resolve_OTIO"] = {"Link Group ID": clip.link_group_id}

    return metadata


def make_clip(clip: TimelineClip, fps: float, file_url_mode: FileUrlMode) -> otio.schema.Clip:
    item = clip.item
    name = clip_name(clip)
    item_range = item.range(fps)
    media_reference = otio.schema.ExternalReference(
        target_url=file_url_for_path(item.path, file_url_mode),
        available_range=item_range,
    )
    media_reference.name = name
    return otio.schema.Clip(
        name=name,
        media_reference=media_reference,
        source_range=item_range,
        metadata=clip_metadata(clip, fps, file_url_mode),
    )


def build_global_sequence_tracks(
    clips: list[TimelineClip],
    fps: float,
    file_url_mode: FileUrlMode,
) -> dict[tuple[str, str, str], otio.schema.Track]:
    tracks = create_tracks(clips)
    track_lengths = {key: 0 for key in tracks}
    global_cursor = 0

    grouped_by_source: dict[Path, list[TimelineClip]] = {}
    for clip in clips:
        grouped_by_source.setdefault(clip.item.path.resolve(), []).append(clip)

    source_order = sorted(
        grouped_by_source,
        key=lambda path: (
            grouped_by_source[path][0].sort_time,
            str(path).lower(),
        ),
    )

    for source_path in source_order:
        source_clips = sorted(
            grouped_by_source[source_path],
            key=lambda clip: (clip.role != "visual", clip.track_key),
        )
        event_duration = max(clip.duration_frames for clip in source_clips)

        for clip in source_clips:
            track = tracks[clip.track_key]
            current_track_length = track_lengths[clip.track_key]
            if current_track_length < global_cursor:
                track.append(make_gap(global_cursor - current_track_length, fps))
            track.append(make_clip(clip, fps, file_url_mode))
            track_lengths[clip.track_key] = global_cursor + clip.duration_frames

        global_cursor += event_duration

    for key, track in tracks.items():
        current_track_length = track_lengths[key]
        if current_track_length < global_cursor:
            track.append(make_gap(global_cursor - current_track_length, fps))

    return tracks


def build_per_track_sequence_tracks(
    clips: list[TimelineClip],
    fps: float,
    file_url_mode: FileUrlMode,
) -> dict[tuple[str, str, str], otio.schema.Track]:
    tracks = create_tracks(clips)
    grouped = {key: [] for key in tracks}
    for clip in clips:
        grouped[clip.track_key].append(clip)

    track_lengths: dict[tuple[str, str, str], int] = {}
    max_length = 0

    for key, track_clips in grouped.items():
        track = tracks[key]
        track_length = 0
        for clip in sorted(track_clips, key=lambda clip: (clip.sort_time, clip.item.path.name.lower())):
            track.append(make_clip(clip, fps, file_url_mode))
            track_length += clip.duration_frames
        track_lengths[key] = track_length
        max_length = max(max_length, track_length)

    for key, track in tracks.items():
        if track_lengths[key] < max_length:
            track.append(make_gap(max_length - track_lengths[key], fps))

    return tracks


def build_timeline_from_plan(
    manifest: dict,
    plan: dict,
    skipped: list[SkippedMedia],
    clips: list[TimelineClip],
    timeline_name: str,
    file_url_mode: FileUrlMode,
    progress: ProgressReporter,
) -> otio.schema.Timeline:
    if not clips:
        raise RuntimeError("No timeline clips could be created from the discovered media.")

    settings = manifest["settings"]
    plan_settings = plan["settings"]
    fps = float(settings["fps"])
    image_duration_seconds = float(settings["image_duration_seconds"])
    layout_mode = plan_settings["layout_mode"]
    audio_mode = plan_settings["audio_mode"]
    resolve_layout = plan_settings["resolve_layout"]
    resolve_link_start_id = int(plan_settings["resolve_link_start_id"])

    timeline = otio.schema.Timeline(name=timeline_name)
    timeline.global_start_time = rational_time(0, fps)
    timeline.metadata["folder_to_otio"] = {
        "timeline_fps": fps,
        "image_duration_seconds": image_duration_seconds,
        "layout_mode": layout_mode,
        "resolve_layout": resolve_layout,
        "audio_mode": audio_mode,
        "file_url_mode": file_url_mode,
        "resolve_link_start_id": resolve_link_start_id,
        "resolve_audio_linking": (
            "video files with embedded audio create paired video/audio clips with "
            "shared Resolve_OTIO.Link Group ID"
            if audio_mode == "linked"
            else "disabled"
        ),
        "skipped_media": [skipped_media_to_dict(item) for item in skipped],
        "pipeline_schema_versions": {
            "media_manifest": MEDIA_MANIFEST_SCHEMA,
            "track_plan": TRACK_PLAN_SCHEMA,
        },
    }

    if layout_mode == "global-sequence":
        progress.report("Building global sequence tracks.")
        tracks = build_global_sequence_tracks(clips, fps, file_url_mode)
    elif layout_mode == "per-track-sequence":
        progress.report("Building per-track sequence tracks.")
        tracks = build_per_track_sequence_tracks(clips, fps, file_url_mode)
    else:
        raise ValueError(f"Unsupported layout mode: {layout_mode}")

    for key in sorted(
        tracks,
        key=lambda key: (
            tracks[key].kind != otio.schema.TrackKind.Video,
            key[0].lower(),
            key[1],
            key[2],
        ),
    ):
        timeline.tracks.append(tracks[key])

    progress.report(f"Created {len(tracks)} track(s).")
    return timeline
