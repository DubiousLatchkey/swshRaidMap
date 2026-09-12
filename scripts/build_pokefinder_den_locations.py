#!/usr/bin/env python3
"""Build the PokéFinder/SeedSearcher physical-den crosswalk.

The source table is parsed from Leanny's shared NestLocations table. Matching first uses
the immutable (location, common hash, rare hash) tuple, then a minimum-distance
assignment resolves physical dens which share the same two encounter pools.
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment


SOURCE_URL = "https://raw.githubusercontent.com/Leanny/PKHeX_Raid_Plugin/ceb8167c909086ca13df8ffbd04287fbd52f0f64/PKHeX_Raid_Plugin/Nests/NestLocations.cs"
SOURCE_COMMIT = "ceb8167c909086ca13df8ffbd04287fbd52f0f64"

LOCATION_NAMES = {
    0: "Axew's Eye", 1: "Bridge Field", 2: "Dappled Grove",
    3: "Dusty Bowl", 4: "East Lake Axewell", 5: "Giant's Cap",
    6: "Giant's Mirror", 7: "Giant's Seat", 8: "Hammerlocke Hills",
    9: "Lake of Outrage", 10: "Motostoke Riverbank",
    11: "North Lake Miloch", 12: "Rolling Fields",
    13: "South Lake Miloch", 14: "Stony Wilderness",
    15: "Watchtower Ruins", 16: "West Lake Axewell",
    17: "Fields of Honor", 18: "Soothing Wetlands",
    19: "Forest of Focus", 20: "Challenge Beach", 21: "Brawlers' Cave",
    22: "Challenge Road", 23: "Courageous Cavern", 24: "Loop Lagoon",
    25: "Training Lowlands", 26: "Potbottom Desert", 27: "Workout Sea",
    28: "Stepping-Stone Sea", 29: "Insular Sea", 30: "Honeycalm Sea",
    31: "Honeycalm Island", 32: "Slippery Slope",
    33: "Frostpoint Field", 34: "Giant's Bed", 35: "Old Cemetery",
    36: "Snowslide Slope", 37: "Path to the Peak", 38: "Crown Shrine",
    39: "Giant's Foot", 40: "Frigid Sea", 41: "Three-Point Pass",
    42: "Ballimere Lake", 43: "Dyna Tree Hill",
}

ROW_RE = re.compile(
    r"new\s+NestHashDetail\(\s*(0x[0-9a-fA-F]+),\s*"
    r"(0x[0-9a-fA-F]+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\)"
)


def region_fields(index: int) -> tuple[str, str, int]:
    if index < 100:
        return "wild_area", "wa", index + 1
    if index < 190:
        return "isle_of_armor", "ioa", index - 99
    # SeedSearcher omits the six Slippery Slope (DLC_32) entries from its
    # Crown Tundra selector. Keep those dens addressable under an explicit
    # non-SeedSearcher prefix, then number Frostpoint Field onward exactly as
    # SeedSearcher does: Frostpoint Field 1 is TC 1.
    if index < 196:
        return "crown_tundra", "ss", index - 189
    return "crown_tundra", "ct", index - 195


def fit_coordinate_transforms(old_groups: dict, new_groups: dict) -> dict[str, np.ndarray]:
    """Fit source-map -> local-map affine transforms from unambiguous anchors."""
    anchors: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for group in old_groups:
        if len(old_groups[group]) == len(new_groups[group]) == 1:
            old, new = old_groups[group][0], new_groups[group][0]
            anchors[new["region"]].append((old, new))

    transforms = {}
    for region, pairs in anchors.items():
        source = np.array([
            [new["program_map_x"], new["program_map_y"], 1.0]
            for _, new in pairs
        ])
        target = np.array([[old["MapX"], old["MapY"]] for old, _ in pairs])
        transform, *_ = np.linalg.lstsq(source, target, rcond=None)
        transforms[region] = transform
    return transforms


def load_source(source_path: Path | None) -> str:
    if source_path:
        return source_path.read_text(encoding="utf-8")
    with urllib.request.urlopen(SOURCE_URL) as response:
        return response.read().decode("utf-8")


def parse_source(text: str) -> list[dict]:
    rows = []
    local_counts: Counter[int] = Counter()
    for index, match in enumerate(ROW_RE.finditer(text)):
        common_hash, rare_hash, location, map_x, map_y = match.groups()
        location_id = int(location)
        local_counts[location_id] += 1
        region, prefix, region_number = region_fields(index)
        rows.append({
            "game_slot_index": index,
            "physical_id": f"{prefix}-{region_number:03d}",
            "region": region,
            "program_den_number": region_number if prefix != "ss" else None,
            "location": location_id,
            "location_name": LOCATION_NAMES[location_id],
            "local_number": local_counts[location_id],
            "display_name": f"{LOCATION_NAMES[location_id]} {local_counts[location_id]}",
            "common_hash": common_hash.lower(),
            "rare_hash": rare_hash.lower(),
            "program_map_x": int(map_x),
            "program_map_y": int(map_y),
            "is_special_crystal_den": index == 16,
        })
    if len(rows) != 276:
        raise RuntimeError(f"Expected 276 PokéFinder rows, found {len(rows)}")
    return rows


def match_rows(legacy: list[dict], source: list[dict]) -> tuple[list[dict], dict]:
    ordinary = [row for row in source if not row["is_special_crystal_den"]]
    old_groups: dict[tuple, list[dict]] = defaultdict(list)
    new_groups: dict[tuple, list[dict]] = defaultdict(list)

    def key(row: dict) -> tuple:
        return row["location"], row["common_hash"].lower(), row["rare_hash"].lower()

    for row in legacy:
        old_groups[key(row)].append(row)
    for row in ordinary:
        new_groups[key(row)].append(row)

    if set(old_groups) != set(new_groups):
        raise RuntimeError("Legacy and PokéFinder hash/location groups differ")
    unequal = [group for group in old_groups if len(old_groups[group]) != len(new_groups[group])]
    if unequal:
        raise RuntimeError(f"Group cardinalities differ for {unequal}")

    transforms = fit_coordinate_transforms(old_groups, new_groups)
    result = []
    distances = []
    ambiguous_groups = 0
    for group_key, old_rows in old_groups.items():
        new_rows = new_groups[group_key]
        if len(old_rows) > 1:
            ambiguous_groups += 1
        costs = []
        for old in old_rows:
            transform = transforms[new_rows[0]["region"]]
            costs.append([
                sum((np.array([new["program_map_x"], new["program_map_y"], 1.0]) @ transform
                     - np.array([old["MapX"], old["MapY"]])) ** 2)
                for new in new_rows
            ])
        old_indices, new_indices = linear_sum_assignment(costs)
        for old_i, new_i in zip(old_indices, new_indices):
            old = old_rows[old_i]
            new = new_rows[new_i]
            distance = costs[old_i][new_i] ** 0.5
            distances.append(distance)
            merged = {key: value for key, value in old.items() if key != "den_number"}
            merged.update({
                "physical_id": new["physical_id"],
                "game_slot_index": new["game_slot_index"],
                "region": new["region"],
                "program_den_number": new["program_den_number"],
                "location_name": new["location_name"],
                "local_number": new["local_number"],
                "display_name": new["display_name"],
                "program_map_x": new["program_map_x"],
                "program_map_y": new["program_map_y"],
                "mapping_method": "hashes+location+minimum_coordinate_distance",
                "mapping_group_size": len(old_rows),
                "mapping_distance_pixels": round(distance, 3),
            })
            result.append(merged)

    result.sort(key=lambda row: row["game_slot_index"])
    physical_ids = [row["physical_id"] for row in result]
    if len(result) != 275 or len(set(physical_ids)) != 275:
        raise RuntimeError("Crosswalk is not a 275-by-275 bijection")

    stats = {
        "ordinary_den_count": len(result),
        "ambiguous_hash_location_groups_resolved_spatially": ambiguous_groups,
        "maximum_mapping_distance_pixels": round(max(distances), 3),
        "mean_mapping_distance_pixels": round(sum(distances) / len(distances), 3),
        "maximum_ambiguous_group_distance_pixels": round(max(
            row["mapping_distance_pixels"] for row in result
            if row["mapping_group_size"] > 1
        ), 3),
        "coordinate_matching": "per-region affine transform fitted from unique hash/location anchors",
    }
    return result, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pokefinder-source", type=Path)
    parser.add_argument("--legacy", type=Path, default=Path("data/den_locations.json"))
    parser.add_argument("--output", type=Path, default=Path("data/den_locations_pokefinder.json"))
    parser.add_argument("--reference-output", type=Path,
                        default=Path("data/reference/pokefinder_den_locations.json"))
    args = parser.parse_args()

    source_rows = parse_source(load_source(args.pokefinder_source))
    legacy = json.loads(args.legacy.read_text(encoding="utf-8"))
    mapped, stats = match_rows(legacy, source_rows)

    reference = {
        "source": SOURCE_URL,
        "source_commit": SOURCE_COMMIT,
        "retrieved_at": "2026-09-11",
        "notes": "Normalized Leanny physical den table shared by SeedSearcher and PokéFinder; includes Watchtower special slot.",
        "dens": source_rows,
    }
    output = {
        "schema_version": 1,
        "generated_at": "2026-09-11",
        "source": {
            "map_locations": "data/den_locations.json",
            "program_locations": "data/reference/pokefinder_den_locations.json",
        },
        "validation": stats,
        "dens": mapped,
    }

    args.reference_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.reference_output.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
