#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import timeline_pipeline_core as core


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a media manifest for Resolve-unsafe same-stem file collisions."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Input media manifest JSON path.")
    parser.add_argument("--output", type=Path, default=None, help="Optional validation report JSON path.")
    parser.add_argument("--quiet", action="store_true", help="Suppress human-readable collision details.")
    args = parser.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    output = args.output.expanduser().resolve() if args.output else None
    _, items, _ = core.load_manifest(manifest_path)

    report = core.validation_report_for_items(items)
    if output is not None:
        if output.suffix.lower() != ".json":
            raise ValueError("Output file must end with .json")
        core.write_json(output, report)

    if report["ok"]:
        if not args.quiet:
            print("Media name validation: OK")
            if output is not None:
                print(f"Report: {output}")
        return

    if not args.quiet:
        print("Media name validation: Resolve-unsafe same-stem collisions found", file=sys.stderr)
        for index, collision in enumerate(report["collisions"], start=1):
            print(f"{index}. Stem: {collision['stem']}", file=sys.stderr)
            print(f"   Folder: {collision['folder']}", file=sys.stderr)
            for item in collision["items"]:
                print(f"   Current:  {item['path']}", file=sys.stderr)
                print(f"   Proposed: {item['proposed_path']}", file=sys.stderr)
        if output is not None:
            print(f"Report: {output}", file=sys.stderr)

    sys.exit(2)


if __name__ == "__main__":
    main()
