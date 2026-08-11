# Feature: Chainable Timeline Scripts

The `scripts/` folder contains separate timeline pipeline commands derived from
the production generator behavior. They are intended for future CLI and GUI
workflows that need to run, inspect, or repeat individual steps.

`generate_timeline.py` remains unchanged and remains the one-shot production
entrypoint.

## Pipeline

Run the chain in four steps:

```bash
python3 scripts/scan-media.py "/path/to/media-root" \
  --output media_manifest.json

python3 scripts/validate-media-names.py \
  --manifest media_manifest.json \
  --output validation_report.json

python3 scripts/plan-timeline-tracks.py \
  --manifest media_manifest.json \
  --output track_plan.json \
  --audio linked

python3 scripts/write-otio-timeline.py \
  --manifest media_manifest.json \
  --track-plan track_plan.json \
  --output generated_timeline.otio
```

Each step prints a human summary to `stdout`. Machine-readable data is written
to explicit JSON files so another CLI or GUI can inspect and reuse it.

## One-Shot Pipeline Wrapper

`generate_timeline_pipeline.py` is a root-level alternative to
`generate_timeline.py`. It exposes a similar one-shot CLI, but internally calls
the chainable scripts as subprocesses:

```bash
python3 generate_timeline_pipeline.py "/path/to/media-root" \
  --output generated_timeline.otio
```

The wrapper uses temporary intermediate artifacts by default. Keep them for
inspection with:

```bash
python3 generate_timeline_pipeline.py "/path/to/media-root" \
  --output generated_timeline.otio \
  --keep-artifacts
```

Use `--artifact-dir DIR` to choose where `media_manifest.json`,
`validation_report.json`, and `track_plan.json` are written. Passing
`--artifact-dir` implies keeping the artifacts.

## Scripts

- `scripts/scan-media.py` probes source media and writes `media_manifest_v1`.
- `scripts/validate-media-names.py` reads a manifest and writes
  `validation_report_v1` with Resolve-unsafe same-stem filename collisions.
- `scripts/plan-timeline-tracks.py` reads a manifest and writes
  `track_plan_v1` with track groups, clip roles, durations, and Resolve link
  group IDs.
- `scripts/write-otio-timeline.py` reads a manifest and track plan, validates
  same-stem collisions again, and writes the final `.otio` file.

## JSON Artifacts

`media_manifest_v1` stores absolute source paths, source folders, media kind,
codec, dimensions, aspect ratio, audio profile, duration, sort timestamp,
skipped-media details, and scan settings.

`validation_report_v1` stores whether the media set is Resolve-safe, the number
of same-stem collisions, current paths, media kinds, and proposed role-suffixed
rename targets.

`track_plan_v1` stores timeline settings, track names, track kinds, clip names,
clip roles, track keys, durations, source paths, and Resolve link group IDs.

## Behavior Notes

- Source media is never renamed or modified.
- `write-otio-timeline.py` refuses Resolve-unsafe same-stem collisions unless
  `--allow-stem-collisions` is passed.
- The scripts share duplicated generator behavior through
  `scripts/timeline_pipeline_core.py`; they do not import or modify
  `generate_timeline.py`.
- `generate_timeline_pipeline.py` orchestrates these scripts as subprocesses.
- The chainable scripts are meant to be inspectable building blocks. For a
  single-command workflow, use `generate_timeline.py`.
