# Finding: Linked Video and Audio Clips in DaVinci Resolve OTIO Import

## Finding

DaVinci Resolve 21 can import separate OTIO video and audio clips as linked editor clips when both clips:

- are placed on matching video and audio tracks;
- reference the same source media file;
- cover the same source range;
- contain the same Resolve-specific link metadata:

```json
{
  "Resolve_OTIO": {
    "Link Group ID": 2
  }
}
```

The observed linking behavior comes from DaVinci Resolve's OTIO importer. It is not a standard OTIO linked-clip schema.

## Minimal OTIO Shape

The smallest working structure is:

- one `Timeline`;
- one video `Track` containing one video `Clip`;
- one audio `Track` containing one audio `Clip`;
- both clips use an `ExternalReference` pointing to the same `.MOV` or `.MP4`;
- both clips use the same `source_range`;
- both clips use the same `Resolve_OTIO.Link Group ID`.

OTIO still needs a time range for each clip, but the time rate is not what links the clips. The link is produced by the shared Resolve metadata plus matching media/range structure.

## Minimal Python Example

This example is intentionally independent of the project generator. It only demonstrates the linked video/audio behavior.

```python
from pathlib import Path

import opentimelineio as otio


source_media = Path("/path/to/source.mov").resolve()
output_otio = Path("linked_video_audio_test.otio").resolve()

link_group_id = 2

source_range = otio.opentime.TimeRange(
    start_time=otio.opentime.RationalTime(0, 24),
    duration=otio.opentime.RationalTime(240, 24),
)

media_reference = otio.schema.ExternalReference(
    target_url=source_media.as_uri(),
    available_range=source_range,
)

video_clip = otio.schema.Clip(
    name=source_media.name,
    media_reference=media_reference,
    source_range=source_range,
    metadata={
        "Resolve_OTIO": {
            "Link Group ID": link_group_id,
        },
    },
)

audio_clip = otio.schema.Clip(
    name=source_media.name,
    media_reference=media_reference,
    source_range=source_range,
    metadata={
        "Resolve_OTIO": {
            "Link Group ID": link_group_id,
        },
    },
)

video_track = otio.schema.Track(
    name="V1",
    kind=otio.schema.TrackKind.Video,
)
audio_track = otio.schema.Track(
    name="A1",
    kind=otio.schema.TrackKind.Audio,
)

video_track.append(video_clip)
audio_track.append(audio_clip)

timeline = otio.schema.Timeline(name="Linked Video Audio Test")
timeline.tracks.append(video_track)
timeline.tracks.append(audio_track)

otio.adapters.write_to_file(timeline, str(output_otio))
```

The numeric values in `RationalTime(...)` only define the example clip range.
They are not part of the Resolve linking mechanism.

## Tested Result

Tested with DaVinci Resolve 21 using one iPhone `.MOV` containing video and embedded audio.

Observed result:

- Resolve imported one video clip on V1.
- Resolve imported one audio clip on A1.
- The audio waveform appeared.
- Resolve treated the video and audio clips as linked.

## References

- OpenTimelineIO overview: OTIO stores editorial data and externally references video/audio media; it does not embed media samples.  
  https://opentimelineio.readthedocs.io/en/stable/
- OpenTimelineIO schema API: `Timeline`, `Track`, `Clip`, `ExternalReference`, and metadata are the relevant schema concepts.  
  https://opentimelineio.readthedocs.io/en/stable/api/python/opentimelineio.schema.html
- OpenTimelineIO linked/grouped clips issue: core OTIO does not currently define a standard linked-clip relationship; metadata is the available path for custom adapter behavior.  
  https://github.com/AcademySoftwareFoundation/OpenTimelineIO/issues/343

## Caveat

`Resolve_OTIO.Link Group ID` should be treated as DaVinci Resolve importer behavior, not portable OTIO behavior. Re-test this finding when changing Resolve versions or targeting another editor.
