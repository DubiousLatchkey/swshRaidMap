#!/usr/bin/env python3
"""Rename legacy numeric den-image folders to canonical physical den IDs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import uuid4


def mappings(crosswalk_path: Path, reverse: bool) -> list[tuple[str, str]]:
    payload = json.loads(crosswalk_path.read_text(encoding="utf-8"))
    pairs = [
        (str(row["legacy_den_number"]), row["physical_id"])
        for row in payload["dens"]
    ]
    if len(pairs) != 275 or len({a for a, _ in pairs}) != 275 or len({b for _, b in pairs}) != 275:
        raise RuntimeError("Crosswalk must contain a 275-by-275 bijection")
    return [(b, a) for a, b in pairs] if reverse else pairs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", type=Path, default=Path("data/den_locations_pokefinder.json"))
    parser.add_argument("--images", type=Path, default=Path("den_images"))
    parser.add_argument("--apply", action="store_true", help="Perform the rename; default is a dry run")
    parser.add_argument("--reverse", action="store_true", help="Restore canonical folders to legacy numbers")
    args = parser.parse_args()

    root = args.images.resolve()
    if not root.is_dir():
        raise RuntimeError(f"Image root does not exist: {root}")

    pairs = mappings(args.crosswalk, args.reverse)
    missing = [source for source, _ in pairs if not (root / source).is_dir()]
    collisions = [target for _, target in pairs if (root / target).exists()]
    if missing:
        raise RuntimeError(f"Missing {len(missing)} source folders; first: {missing[:5]}")
    if collisions:
        raise RuntimeError(f"Found {len(collisions)} target collisions; first: {collisions[:5]}")

    direction = "canonical -> legacy" if args.reverse else "legacy -> canonical"
    if not args.apply:
        print(f"Dry run OK: {len(pairs)} folders can be renamed ({direction}).")
        print("Re-run with --apply to perform the two-phase rename.")
        return

    token = uuid4().hex
    staged: list[tuple[Path, Path]] = []
    for index, (source, target) in enumerate(pairs):
        temporary = root / f".__den_rename_{token}_{index:03d}"
        (root / source).rename(temporary)
        staged.append((temporary, root / target))

    for temporary, target in staged:
        temporary.rename(target)

    print(f"Renamed {len(pairs)} folders ({direction}).")


if __name__ == "__main__":
    main()
