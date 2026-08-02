# Finding: Same-Stem Media Collisions in DaVinci Resolve OTIO Import

## Finding

DaVinci Resolve 21 was observed to crash during OTIO import when different
source media files in the same folder shared the same filename stem.

Example collision:

```text
IMG_4988.JPG
IMG_4988.MOV
```

In the tested case, one file was a still image and the other was a video with
linked embedded audio. The OTIO was valid and readable by OpenTimelineIO, but
Resolve crashed while selecting or importing the `.otio` file.

## Important Detail

Changing only OTIO display names did not fix the crash.

The following OTIO fields were changed to unique role-qualified values:

```text
Clip.name
ExternalReference.name
```

Example:

```text
IMG_4988__still.JPG
IMG_4988__video.MOV
IMG_4988__linked-audio.MOV
```

Resolve still crashed. This suggests Resolve's importer may collide on the
actual referenced media filename or path, not only on OTIO clip/reference names.

## Tested Result

Tested with DaVinci Resolve 21 using a Satipo sample folder containing:

```text
samples/2025_08_satipo/iphone/IMG_4988.JPG
samples/2025_08_satipo/iphone/IMG_4988.MOV
```

Observed results:

- an OTIO window containing both `IMG_4988.JPG` and `IMG_4988.MOV` crashed Resolve;
- the same range without `IMG_4988.JPG` imported correctly;
- the same range without `IMG_4988.MOV` imported correctly;
- videos-only and images-only timelines imported correctly;
- renaming the actual source files to remove the same-stem collision allowed the full timeline to import correctly.

This points to a Resolve importer collision, not corrupt media and not invalid
OTIO.

## Mitigation

Before generating OTIO for DaVinci Resolve, avoid same-folder media files that
share a filename stem across different extensions.

Rename one or both files before generation. A role suffix is easy to inspect:

```text
IMG_4988__still.JPG
IMG_4988__video.MOV
```

The source media path must be the renamed path used in the generated OTIO.
Changing only OTIO clip names is not sufficient for the tested Resolve version.

## Generator Behavior

The project generator detects same-folder same-stem collisions before writing
the OTIO file. By default it stops and prints the detected files with proposed
rename targets.

Use the override only for debugging or non-Resolve targets:

```bash
python generate_timeline.py /path/to/media-root \
  --output out/debug.otio \
  --allow-stem-collisions
```

For DaVinci Resolve, the recommended action is to rename the source media and
regenerate the OTIO.

## References

- OpenTimelineIO overview: OTIO stores editorial data and externally references
  media; it does not embed media samples.  
  https://opentimelineio.readthedocs.io/en/stable/
- OpenTimelineIO schema API: `Clip` and `ExternalReference` are the relevant
  schema objects for clip names and media references.  
  https://opentimelineio.readthedocs.io/en/stable/api/python/opentimelineio.schema.html

## Caveat

This is observed DaVinci Resolve importer behavior, not an OpenTimelineIO rule.
Re-test when changing Resolve versions or targeting another editor.
