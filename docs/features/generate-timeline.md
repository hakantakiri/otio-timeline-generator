# Feature: Generate Timeline

`generate_timeline.py` is the production OpenTimelineIO (`.otio`) timeline
generator. It creates Resolve-oriented timelines from folders of images, videos,
and audio files for import into tools that support OTIO, especially DaVinci
Resolve.

## Use Case

Given a root folder containing one or more source folders, generate a
chronological timeline where supported image, video, and audio files are grouped
onto organized tracks.

Visual tracks are grouped by source folder, media type, and aspect ratio.
Standalone audio tracks are grouped by source folder, audio media type, and
audio profile, such as channel layout plus sample rate. Video files with
embedded audio can also create Resolve-linked companion audio clips.

The script can be used directly from the CLI. Its implementation is structured
around reusable functions such as `build_source_items`, `build_timeline`, and
the layout builders so the same behavior can be imported into a library workflow
later.

## Input Folder Model

The input path is treated as a root folder.

- If the root contains child folders, each immediate child folder becomes a
  source folder.
- If the root does not contain child folders, the root itself becomes the only
  source folder.
- By default, only files directly inside each source folder are scanned.
- Pass `--recursive` to scan nested folders under each source folder.

Example:

```text
Trip Media/
  Day 01/
    IMG_0001.JPG
    IMG_0002.JPG
    CLIP_0001.MOV
  Day 02/
    IMG_0101.JPG
    CLIP_0101.MP4
```

This produces visual tracks based on folder name, media type, and detected
aspect ratio. Standalone audio files also produce audio tracks when present:

```text
Day 01 - Images - 3:2
Day 01 - Videos - 16:9
Day 01 - Audio - Stereo 48kHz
Day 02 - Images - 3:2
Day 02 - Videos - 9:16
Day 02 - Audio - Mono 48kHz
```

## Supported Media

Supported image extensions:

```text
.jpg, .jpeg, .png, .tif, .tiff, .heic, .webp
```

Supported video extensions:

```text
.mp4, .mov, .m4v, .avi, .mkv, .mts, .m2ts
```

Supported audio-only extensions:

```text
.wav, .aif, .aiff, .mp3, .m4a, .aac, .flac
```

Image clips use a fixed duration. The default is `3.0` seconds and can be
changed with `--image-duration`.

Video and audio clips use the real media duration reported by `ffprobe`.
Timeline placement, gaps, and clip media ranges are written at the requested
timeline FPS for NLE importer compatibility.

## Aspect Ratio Tracks

Each visual media file is inspected with `ffprobe` to read its first
video/image stream dimensions, display aspect ratio, sample aspect ratio, and
rotation metadata. The track label uses a normalized display aspect ratio, not
just raw coded dimensions.

Examples:

```text
3840x2160 -> 16:9
2160x3840 -> 9:16
4032x3024 -> 4:3
6000x4000 -> 3:2
```

The aspect ratio is part of the track key, so files from the same folder and
media type are still split when their displayed shapes differ.

Example track names:

```text
Day 01 - Images - 3:2
Day 01 - Images - 9:16
Day 01 - Videos - 16:9
Day 01 - Videos - 9:16
```

For rotated video files, the script checks rotation metadata reported by
`ffprobe` and adjusts the displayed ratio for 90-degree or 270-degree rotations
before assigning the track.

The script normalizes near-matches into common ratio buckets:

```text
16:9, 9:16, 4:3, 3:4, 3:2, 2:3, 1:1
```

Unusual formats keep a deterministic reduced fallback label.

## Audio Handling

The generated `.otio` file references source media externally; it does not
embed audio samples.

By default, `--audio linked` is used for DaVinci Resolve compatibility:

- Video files with embedded audio create paired video and audio clips.
- Both paired clips reference the same source media and use the same source
  range.
- Both paired clips receive the same `Resolve_OTIO.Link Group ID`.
- Standalone supported audio files are imported as OTIO audio tracks.
- Embedded audio stream count and audio profile are preserved in clip metadata.

Standalone audio tracks are grouped by:

```text
source folder + Audio + audio profile
```

The audio profile is derived from channel layout or channel count plus sample
rate. Examples:

```text
Day 01 - Audio - Stereo 48kHz
Day 01 - Audio - Mono 48kHz
Day 02 - Audio - 6ch 48kHz
```

Audio modes:

```text
linked      Default. Create Resolve-linked V/A pairs for embedded-audio videos and import standalone audio files.
standalone  Import video/images plus standalone audio files only.
none        Do not create audio tracks.
```

`--resolve-layout organized` keeps linked embedded-audio clips grouped by audio
profile. `--resolve-layout linked-pairs` groups linked embedded-audio clips by
the matching video folder/aspect track.

`--resolve-link-start-id` controls the first numeric Resolve link group ID. The
default is `2`, matching the tested Resolve finding.

## Resolve Embedded-Audio Experiment

`examples/resolve-audio-otio-experiment.py` creates a minimal compatibility test
for Resolve. It takes one video file with embedded audio and writes an OTIO
timeline containing:

- one video track with one clip referencing the source video;
- one audio track with one matching clip referencing the same source video;
- matching start time and duration on both tracks;
- optional Resolve link metadata.

Example:

```bash
python3 examples/resolve-audio-otio-experiment.py \
  samples/2025_08_satipo/iphone/IMG_4950.MOV \
  --output out/resolve_audio_experiment_img_4950.otio
```

Use this only to test Resolve's OTIO importer on a tiny file. If this minimal
file crashes Resolve or imports without usable audio, use
`generate_timeline.py --audio linked` for the production generator and consider
another interchange path, such as FCPXML or Resolve scripting, for a stronger
linked-audio workflow.

## Chronological Ordering

Each media file is sorted by the best available timestamp:

1. `exiftool` metadata, when available.
2. File modification time, when metadata cannot be read.

The metadata fields checked are:

```text
DateTimeOriginal
CreateDate
MediaCreateDate
TrackCreateDate
FileModifyDate
```

When two files have the same timestamp, the script uses folder name, media kind,
aspect ratio or audio profile, and file name as stable tie-breakers.

## Resolve File URL Mode

By default, `--file-url-mode resolve` writes local file URLs with path spaces
left unescaped:

```text
file:///path/to/dji neo/source.MP4
```

This is more compatible with DaVinci Resolve than standards-style
percent-encoded file URLs in tested cases. Use `--file-url-mode encoded` when
strict URI encoding is needed for non-Resolve OTIO tooling.

This behavior is documented in
`docs/findings/davinci-resolve-file-url-path-encoding-otio.md`.

## Resolve Same-Stem Collision Preflight

DaVinci Resolve 21 was observed to crash when an OTIO references different media
files in the same folder that share the same filename stem, such as:

```text
IMG_4988.JPG
IMG_4988.MOV
```

Changing only OTIO clip names did not fix the tested crash. Renaming the actual
source media files did.

The root generator detects these collisions before writing the `.otio` file. By
default, it stops and prints proposed role-based names:

```text
IMG_4988__still.JPG
IMG_4988__video.MOV
```

Review the proposed names, rename the source files, and run the generator again.
Use `--allow-stem-collisions` only for debugging or for non-Resolve targets.

This behavior is documented in
`docs/findings/davinci-resolve-same-stem-media-collision-otio.md`.

## Layout Modes

The script supports two timeline layout modes.

### `global-sequence`

This is the default mode.

All media is sorted into one global chronological sequence across all folders.
Each clip is placed on its corresponding grouped track, and gaps are inserted on
other tracks so every source event lands at its chronological timeline position.

Use this when you want one chronological assembly with no unintended overlap.
Linked audio from a video file is aligned with that video file rather than being
treated as a separate later event.

```bash
python3 generate_timeline.py "/path/to/Trip Media" \
  --output trip.otio
```

### `per-track-sequence`

Each grouped track is ordered independently and starts at timeline frame 0.
Clips from different tracks can overlap in time because every track builds its
own sequence.

Use this when you want organized lanes for manual editing rather than one
continuous chronological assembly.

```bash
python3 generate_timeline.py "/path/to/Trip Media" \
  --output trip_by_track.otio \
  --layout-mode per-track-sequence
```

## CLI Usage

```bash
python3 generate_timeline.py INPUT_ROOT --output PATH [options]
```

Options:

```text
--output PATH                    Required output .otio path.
--timeline-name NAME             Timeline name inside the OTIO file.
--fps FPS                        OTIO timing rate. Default: 24.
--image-duration SECONDS         Still image duration. Default: 3.
--recursive                      Search recursively inside each source folder.
--layout-mode MODE               global-sequence or per-track-sequence.
--audio MODE                     linked, standalone, or none.
--resolve-layout MODE            organized or linked-pairs.
--resolve-link-start-id INTEGER  First Resolve_OTIO Link Group ID. Default: 2.
--file-url-mode MODE             resolve or encoded.
--allow-stem-collisions          Force generation despite Resolve-unsafe same-stem media names.
--strict                         Fail on the first media file that cannot be probed.
--quiet                          Suppress progress output.
```

Full example:

```bash
python3 generate_timeline.py ~/Movies/Trip \
  --output ~/Desktop/trip.otio \
  --timeline-name "Trip Selects" \
  --fps 24 \
  --image-duration 2.5 \
  --recursive \
  --layout-mode global-sequence \
  --audio linked \
  --file-url-mode resolve
```

During execution, the script prints progress to `stderr`: source folder
discovery, file collection, per-file probe progress, skipped files, timeline
track creation, and OTIO write status. Use `--quiet` to suppress progress
output.

On success, the script prints the generated file path, timeline name, source
item count, visual clip count, linked audio clip count, standalone audio clip
count, track count, frame rate, image duration, layout mode, audio mode, Resolve
layout, Resolve link start ID, file URL mode, and skipped-media count to
`stdout`.

By default, unreadable or unsupported-by-probe media files are skipped and
reported at the end of the run. Use `--strict` when you want the command to fail
immediately instead.

## Dependencies

Required:

- Python 3
- `opentimelineio`
- `ffprobe`, provided by FFmpeg, for media dimensions, video durations, audio
  stream details, and audio durations

Recommended:

- `exiftool`, for capture/create time metadata

On macOS, the external tools can be installed with:

```bash
brew install ffmpeg exiftool
```

If `ffprobe` is missing, timeline generation fails because track grouping and
timing require stream metadata. If `exiftool` is missing or cannot read a file,
the script falls back to file modification time.

## Generated OTIO Details

The generated timeline:

- Starts at frame 0.
- Uses the requested FPS for timeline placement, gaps, still duration, clip
  ranges, and timeline metadata.
- Creates video tracks for visual media.
- Creates audio tracks for linked embedded-audio clips and standalone audio
  files when `--audio linked` is selected.
- References source media through absolute file URLs.
- Stores source path, source folder, media kind, clip role, grouping label,
  codec, raw dimensions, display dimensions, aspect ratio, embedded-audio
  summary fields, audio profile fields, duration, file URL mode, Resolve link
  group ID, and sort timestamp in clip metadata under `folder_to_otio`.
- Stores timeline FPS, image duration, layout mode, Resolve layout, audio mode,
  file URL mode, Resolve link start ID, Resolve audio-linking policy, and
  skipped-media details in timeline metadata under `folder_to_otio`.

Still images use an explicit compatibility convention: the image
`ExternalReference.available_range` and clip `source_range` both match the
requested still duration at the timeline FPS. This keeps the generated clip
duration simple for adapter import, but should still be validated against the
target NLE.

Resolution is not enforced by OTIO. For DaVinci Resolve, set the intended
timeline resolution and frame rate during or before OTIO import.

## Error Conditions

The script raises an error when:

- The input root does not exist.
- The input root is not a directory.
- The output path does not end in `.otio`.
- No supported media is found.
- `ffprobe` is unavailable when media dimensions or timing need to be read.
- `--strict` is enabled and `ffprobe` cannot probe a supported media file.
- All supported media files are skipped or no timeline clips can be created.
- Resolve-unsafe same-stem media collisions are found and
  `--allow-stem-collisions` is not set.

## Current Limitations

- Images all use one fixed duration.
- Aspect-ratio grouping uses normalized display ratios, not semantic labels such
  as landscape, portrait, or square.
- Resolve behavior depends on the target Resolve version and its OTIO importer.
- Standalone audio grouping uses technical stream profile labels, not semantic
  labels such as dialogue, music, or ambience.
- Resolution is not enforced by OTIO.
- Timeline import behavior depends on the target application and its OTIO
  support.
