# Finding: File URL Path Encoding in DaVinci Resolve OTIO Import

## Finding

DaVinci Resolve can fail to locate media during OTIO import when a local file
path in `ExternalReference.target_url` contains percent-encoded spaces.

Example standards-style file URL:

```text
file:///path/to/dji%20neo/source.MP4
```

Resolve may report the file as missing even when the actual file exists at:

```text
/path/to/dji neo/source.MP4
```

For Resolve-targeted OTIO files, a more compatible local file URL keeps path
spaces unescaped:

```text
file:///path/to/dji neo/source.MP4
```

This is DaVinci Resolve importer compatibility behavior. It is not a general
OTIO recommendation, because percent-encoded paths are the standards-compliant
URI form.

## Minimal Python Example

This example only demonstrates local file URL construction for an OTIO media
reference.

```python
from pathlib import Path

import opentimelineio as otio


source_media = Path("/path/to/dji neo/source.MP4").resolve()

encoded_url = source_media.as_uri()
resolve_url = "file://" + str(source_media)

media_reference = otio.schema.ExternalReference(
    target_url=resolve_url,
)
```

Use `encoded_url` when strict URI encoding is needed for OTIO tooling. Use
`resolve_url` when generating OTIO files intended for DaVinci Resolve import.

## Tested Result

Tested with a DJI `.MP4` file in a path containing a space.

Observed result:

- the standards-style URL wrote the folder as `dji%20neo`;
- the Resolve-compatible URL wrote the folder as `dji neo`;
- OpenTimelineIO can store and read both forms;
- DaVinci Resolve may report the percent-encoded form as missing media.

The DJI file used for this test had no embedded audio, so the generated OTIO was
video-only. The file URL behavior is independent of whether the media has audio.

## References

- OpenTimelineIO schema API: `ExternalReference.target_url` is the media target
  field used by clips.  
  https://opentimelineio.readthedocs.io/en/stable/api/python/opentimelineio.schema.html
- Python `Path.as_uri()`: converts an absolute path to a file URI using URI
  escaping rules.  
  https://docs.python.org/3/library/pathlib.html#pathlib.PurePath.as_uri

## Caveat

Raw spaces in a file URL are a Resolve compatibility choice. Re-test this
behavior when changing Resolve versions or targeting another editor.
