# OTIO Timeline Generator

Generate OpenTimelineIO (`.otio`) timelines from folders of images, videos, and audio files.

The example script builds a chronological timeline from media folders and groups
visual tracks by source folder, media type, and normalized aspect ratio. Audio
tracks are grouped by source folder and audio profile, such as channel layout
plus sample rate. Generated OTIO files can be imported into tools that support
OpenTimelineIO, such as DaVinci Resolve.

## System Dependencies

Install the media-probing tools first:

```bash
brew install ffmpeg exiftool
```

Required:

- `ffprobe`, installed with FFmpeg. The script uses it to read media dimensions,
  display aspect ratio, frame rate, audio stream details, and duration.

Recommended:

- `exiftool`. The script uses it to sort media by capture/create metadata. If it
  is missing or cannot read a file, the script falls back to file modification
  time.

Verify the tools:

```bash
ffprobe -version
exiftool -ver
```

## Python Environment with uv

Use a project-local virtual environment. Prefer `uv` over stdlib `venv`.

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install opentimelineio
```

Verify the Python dependency:

```bash
python -c "import opentimelineio as otio; print(otio.__version__)"
```

Python `3.10+` is required because the script uses modern type syntax. Python
`3.11+` is recommended.

## Run the Example

Show CLI options:

```bash
python examples/generate-timeline.py --help
```

Generate a timeline:

```bash
python examples/generate-timeline.py "/path/to/media-root" \
  --output generated_timeline.otio
```

Fail immediately when any media file cannot be probed:

```bash
python examples/generate-timeline.py "/path/to/media-root" \
  --output generated_timeline.otio \
  --strict
```

## Input Folder Shape

The input path is treated as a media root.

- If the root contains child folders, each immediate child folder becomes a
  source folder.
- If the root has no child folders, the root itself becomes the only source
  folder.
- By default, only files directly inside each source folder are scanned.
- Use `--recursive` to scan nested media under each source folder.

Example:

```text
Trip Media/
  Day 01/
    IMG_0001.JPG
    CLIP_0001.MOV
  Day 02/
    IMG_0101.JPG
    CLIP_0101.MP4
```

Example generated track names:

```text
Day 01 - Images - 3:2
Day 01 - Videos - 16:9
Day 01 - Audio - Stereo 48kHz
Day 02 - Images - 3:2
Day 02 - Videos - 9:16
Day 02 - Audio - Mono 48kHz
```

## Before Testing on Real Media

Use this checklist before running against an important media folder:

- Activate the project environment with `source .venv/bin/activate`.
- Confirm `ffprobe -version` works.
- Install `exiftool` if chronological order should use capture metadata.
- Run the script first on a small copied sample folder.
- Review any skipped-media output before trusting the generated timeline.
- Fix same-folder filename-stem collisions before importing into Resolve, such
  as `IMG_0001.JPG` and `IMG_0001.MOV`. The generator reports these because
  Resolve 21 was observed to crash on OTIO import until the source files were
  renamed.
- Use `--strict` when media probe failures should stop the run.
- Use `--quiet` when scripting and you only want the final summary.
- The default `--audio-mode linked` is the Resolve-safe mode: video files are
  referenced once and standalone audio files are imported as audio tracks.

## Generated Timeline Behavior

- Visual tracks are grouped by source folder, media type, and normalized aspect
  ratio.
- Standalone audio tracks are grouped by source folder, audio media type, and
  audio profile.
- Video media references use timeline-FPS timing for better NLE importer
  compatibility. Native frame rate is preserved in clip metadata.
- Embedded audio in video files is left in the source video container by default
  instead of being split into separate OTIO audio clips.
- Audio-only files are imported as independent audio clips.
- Audio media references use timeline-FPS timing for better NLE importer
  compatibility. Native sample rate is preserved in clip metadata.
- Timeline placement, gaps, still duration, and timeline metadata use the
  requested timeline FPS.
- Still images use `--image-duration`.
- Progress is printed to `stderr` while probing media, building tracks, and
  writing the `.otio` file. The final summary is printed to `stdout`.
- `--audio-mode` can use Resolve-safe linked mode, standalone audio only, no
  audio tracks, or experimental split embedded audio.
- Resolution is written as metadata only. Set the project/timeline resolution in
  the target NLE during or before OTIO import.

## Resolve Embedded-Audio Experiment

Use `examples/resolve-audio-otio-experiment.py` to test whether your Resolve
version can import one video file as both video and timeline audio:

```bash
python examples/resolve-audio-otio-experiment.py \
  samples/2025_08_satipo/iphone/IMG_4950.MOV \
  --output out/resolve_audio_experiment_img_4950.otio
```

The generated file intentionally contains one video track and one audio track
that both reference the same `.MOV/.MP4` container. Open it in Raven first, then
import only this small file into a fresh Resolve project. If Resolve crashes or
imports video-only, do not use `--audio-mode split-embedded` for full timelines.

## Resolve Linked Video/Audio OTIO

Use `scripts/create-linked-video-audio-otio.py` to create a focused OTIO file
from one video file. When the source contains embedded audio, the generated
timeline contains one video clip and one audio clip that reference the same
source media and share the same Resolve link metadata:

```bash
python scripts/create-linked-video-audio-otio.py \
  /path/to/source.mov \
  --output out/linked_video_audio.otio
```

Videos without embedded audio, such as some drone clips, generate a video-only
OTIO file without Resolve link metadata.

The script defaults to Resolve-compatible local file URLs, keeping path spaces
unescaped in `target_url` values. This avoids Resolve import failures with paths
such as `dji neo`. Use `--file-url-mode encoded` only when you specifically
want standards-style percent-encoded file URIs for OTIO tooling.

This script implements the behavior documented in
`docs/findings/davinci-resolve-linked-video-audio-otio.md`.

## Resolve Same-Stem Media Collisions

DaVinci Resolve can crash when an OTIO references different files in the same
folder with the same filename stem, for example:

```text
IMG_4988.JPG
IMG_4988.MOV
```

Rename the source files before generating the OTIO, using names such as:

```text
IMG_4988__still.JPG
IMG_4988__video.MOV
```

The root generator detects these collisions and stops before writing a
Resolve-targeted OTIO. Use `--allow-stem-collisions` only for debugging or
non-Resolve workflows.

See `docs/findings/davinci-resolve-same-stem-media-collision-otio.md`.
