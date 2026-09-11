#!/usr/bin/env python3
"""Find a minimum Sword/Shield raid-pool plan for a shiny living dex.

The optimization is lexicographic:

1. Cover every Generation VIII species reachable from the raid tables, allowing
   repeated catches and forward evolution.
2. Minimize version-specific common/rare/progression-tier manipulations.
3. Preserve an exact minimum Generation VIII baseline, then cover every reachable
   Generation I-VII target with the fewest additional manipulations.
4. Allow a required additional pool even when it serves only one older target.

Regional and Gigantamax forms are collapsed to National Pokédex species IDs.
The evolution graph runs through Generation IX, so later evolutions such as
Dipplin, Hydrapple, and Archaludon are included.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_matrix, vstack


POKEAPI_GRAPHQL = "https://graphql.pokeapi.co/v1beta2"
POKEAPI_QUERY = """
query SpeciesThroughGenerationNine {
  pokemonspecies(where: {id: {_lte: 1025}}) {
    id
    name
    generation_id
    evolves_from_species_id
  }
}
""".strip()

VERSION_NAMES = {1: "sword", 2: "shield"}


@dataclass
class Pool:
    index: int
    key: str
    version: int
    beam: str
    tier: str
    direct_species: frozenset[int]
    aliases: list[dict[str, int]]
    covers: frozenset[int] = frozenset()


class ConstraintBuilder:
    """Incrementally construct a sparse LinearConstraint."""

    def __init__(self, variable_count: int) -> None:
        self.variable_count = variable_count
        self.rows: list[int] = []
        self.cols: list[int] = []
        self.values: list[float] = []
        self.lower: list[float] = []
        self.upper: list[float] = []

    def add(
        self,
        coefficients: Iterable[tuple[int, float]],
        lower: float = -math.inf,
        upper: float = math.inf,
    ) -> None:
        row = len(self.lower)
        for column, value in coefficients:
            if value:
                self.rows.append(row)
                self.cols.append(column)
                self.values.append(float(value))
        self.lower.append(float(lower))
        self.upper.append(float(upper))

    def build(self) -> LinearConstraint:
        matrix = coo_matrix(
            (self.values, (self.rows, self.cols)),
            shape=(len(self.lower), self.variable_count),
        ).tocsr()
        return LinearConstraint(matrix, np.asarray(self.lower), np.asarray(self.upper))


def parse_args() -> argparse.Namespace:
    script_path = Path(__file__).resolve()
    project_root = script_path.parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=project_root)
    parser.add_argument(
        "--reference",
        type=Path,
        default=project_root / "data" / "reference" / "pokeapi_species_gen9.json",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=project_root / "output" / "raid_coverage"
    )
    parser.add_argument("--refresh-reference", action="store_true")
    parser.add_argument("--min-old-per-pool", type=int, default=1)
    return parser.parse_args()


def fetch_reference(path: Path) -> dict[str, Any]:
    payload = json.dumps({"query": POKEAPI_QUERY}).encode("utf-8")
    request = urllib.request.Request(
        POKEAPI_GRAPHQL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "swshRaidMap-coverage-optimizer/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not fetch PokéAPI reference: {exc}") from exc

    if result.get("errors"):
        raise RuntimeError(f"PokéAPI returned errors: {result['errors']}")
    rows = result.get("data", {}).get("pokemonspecies", [])
    if len(rows) < 1025:
        raise RuntimeError(f"Expected at least 1025 species from PokéAPI; received {len(rows)}")

    rows = sorted(rows, key=lambda item: int(item["id"]))
    reference = {
        "source": {
            "endpoint": POKEAPI_GRAPHQL,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "query": POKEAPI_QUERY,
        },
        "rules": {
            "maximum_generation": 9,
            "maximum_species_id": 1025,
            "forms_collapsed_to_species": True,
        },
        "species": rows,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    return reference


def load_reference(path: Path, refresh: bool) -> dict[str, Any]:
    if refresh or not path.exists():
        print(f"Fetching PokêPI species reference -> {path}", flush=True)
        return fetch_reference(path)
    return json.loads(path.read_text(encoding="utf-8"))


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def pool_signature(species: Iterable[int]) -> str:
    canonical = ",".join(str(value) for value in sorted(set(species)))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:12]


def encounter_species_for_tier(table: dict[str, Any], tier: str) -> tuple[int, ...]:
    probability_indexes = (0, 1) if tier == "early" else (2, 3, 4)
    return tuple(
        sorted(
            {
                int(encounter["species"])
                for encounter in table["pokemon"]
                if any(
                    index < len(encounter.get("Probabilities", []))
                    and encounter["Probabilities"][index] > 0
                    for index in probability_indexes
                )
            }
        )
    )


def build_pools(
    raid_tables: list[dict[str, Any]],
    dens: list[dict[str, Any]],
    early_inaccessible_dens: set[int],
) -> list[Pool]:
    common_ids = {int(den["common_pool"]) for den in dens}
    rare_ids = {int(den["rare_pool"]) for den in dens}
    if common_ids & rare_ids:
        raise ValueError("A pool ID is labeled as both common and rare")

    early_pool_ids = {
        beam: {
            int(den[f"{beam}_pool"])
            for den in dens
            if int(den["den_number"]) not in early_inaccessible_dens
        }
        for beam in ("common", "rare")
    }
    grouped: dict[tuple[int, str, str, tuple[int, ...]], list[dict[str, int]]] = defaultdict(list)
    for record_index, table in enumerate(raid_tables):
        raw_pool_id = table.get("pool_id")
        if raw_pool_id is None:
            continue
        pool_id = int(raw_pool_id)
        if pool_id in common_ids:
            beam = "common"
        elif pool_id in rare_ids:
            beam = "rare"
        else:
            raise ValueError(f"Pool ID {pool_id} is not referenced by den_locations.json")

        for tier in ("early", "late"):
            if tier == "early" and pool_id not in early_pool_ids[beam]:
                continue
            # Forms and Gigantamax factor deliberately collapse to species.
            species = encounter_species_for_tier(table, tier)
            if not species:
                continue
            grouped[(int(table["version"]), beam, tier, species)].append(
                {"pool_id": pool_id, "raid_table_index": record_index}
            )

    pools: list[Pool] = []
    for index, ((version, beam, tier, species), aliases) in enumerate(
        sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3]))
    ):
        signature = pool_signature(species)
        key = f"{VERSION_NAMES[version]}-{beam}-{tier}-{signature}"
        pools.append(
            Pool(
                index=index,
                key=key,
                version=version,
                beam=beam,
                tier=tier,
                direct_species=frozenset(species),
                aliases=sorted(aliases, key=lambda value: (value["pool_id"], value["raid_table_index"])),
            )
        )
    return pools


def build_species_graph(reference: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], dict[int, set[int]]]:
    species = {int(row["id"]): row for row in reference["species"]}
    children: dict[int, set[int]] = defaultdict(set)
    for species_id, row in species.items():
        parent = row.get("evolves_from_species_id")
        if parent is not None and int(parent) in species:
            children[int(parent)].add(species_id)
    return species, children


def descendant_map(species: dict[int, dict[str, Any]], children: dict[int, set[int]]) -> dict[int, frozenset[int]]:
    cache: dict[int, frozenset[int]] = {}

    def visit(start: int, trail: frozenset[int] = frozenset()) -> frozenset[int]:
        if start in cache:
            return cache[start]
        if start in trail:
            raise ValueError(f"Cycle detected in evolution graph at species {start}")
        reached = {start}
        for child in children.get(start, set()):
            reached.update(visit(child, trail | {start}))
        cache[start] = frozenset(reached)
        return cache[start]

    for species_id in species:
        visit(species_id)
    return cache


def attach_coverage(pools: list[Pool], descendants: dict[int, frozenset[int]]) -> None:
    for pool in pools:
        covered: set[int] = set()
        for direct in pool.direct_species:
            covered.update(descendants.get(direct, frozenset({direct})))
        pool.covers = frozenset(covered)


def solve_stage_one(pools: list[Pool], gen8_targets: list[int]) -> tuple[int, np.ndarray]:
    builder = ConstraintBuilder(len(pools))
    for target in gen8_targets:
        candidates = [(pool.index, 1.0) for pool in pools if target in pool.covers]
        if not candidates:
            raise ValueError(f"Generation VIII target {target} has no covering pool")
        builder.add(candidates, lower=1.0)

    result = milp(
        c=np.ones(len(pools)),
        integrality=np.ones(len(pools)),
        bounds=Bounds(np.zeros(len(pools)), np.ones(len(pools))),
        constraints=builder.build(),
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Minimum-pool solve failed: {result.message}")
    selected_count = int(round(float(result.fun)))
    return selected_count, result.x


@dataclass
class StageTwoLayout:
    x_start: int
    z_start: int
    q_start: int
    a_start: int
    variable_count: int
    old_targets: list[int]
    pairs: list[tuple[int, int]]


def build_stage_two_constraints(
    pools: list[Pool],
    gen8_targets: list[int],
    old_targets: list[int],
    minimum_pool_count: int,
    min_old_per_pool: int,
) -> tuple[StageTwoLayout, LinearConstraint]:
    pair_list = [
        (pool.index, target_index)
        for pool in pools
        for target_index, target in enumerate(old_targets)
        if target in pool.covers
    ]
    pool_count = len(pools)
    old_count = len(old_targets)
    layout = StageTwoLayout(
        x_start=0,
        z_start=pool_count,
        q_start=pool_count + old_count,
        a_start=pool_count + old_count + pool_count,
        variable_count=pool_count + old_count + pool_count + len(pair_list),
        old_targets=old_targets,
        pairs=pair_list,
    )
    builder = ConstraintBuilder(layout.variable_count)

    for target in gen8_targets:
        builder.add(((pool.index, 1.0) for pool in pools if target in pool.covers), lower=1.0)
    builder.add(((pool.index, 1.0) for pool in pools), lower=minimum_pool_count, upper=minimum_pool_count)

    pairs_by_target: dict[int, list[int]] = defaultdict(list)
    pairs_by_pool: dict[int, list[int]] = defaultdict(list)
    for pair_index, (pool_index, target_index) in enumerate(pair_list):
        assignment_var = layout.a_start + pair_index
        pairs_by_target[target_index].append(assignment_var)
        pairs_by_pool[pool_index].append(assignment_var)
        builder.add(((assignment_var, 1.0), (pool_index, -1.0)), upper=0.0)  # a <= x
        builder.add(
            ((assignment_var, 1.0), (layout.q_start + pool_index, -1.0)), upper=0.0
        )  # a <= q

    for target_index in range(old_count):
        coefficients = [(var, 1.0) for var in pairs_by_target[target_index]]
        coefficients.append((layout.z_start + target_index, -1.0))
        builder.add(coefficients, lower=0.0, upper=0.0)

    for pool in pools:
        q_var = layout.q_start + pool.index
        builder.add(((q_var, 1.0), (pool.index, -1.0)), upper=0.0)  # q <= x
        coefficients = [(var, 1.0) for var in pairs_by_pool[pool.index]]
        coefficients.append((q_var, -float(min_old_per_pool)))
        builder.add(coefficients, lower=0.0)

    return layout, builder.build()


def solve_stage_two(
    pools: list[Pool],
    layout: StageTwoLayout,
    constraints: LinearConstraint,
) -> tuple[int, np.ndarray]:
    objective = np.zeros(layout.variable_count)
    objective[layout.z_start : layout.q_start] = -1.0
    result = milp(
        c=objective,
        integrality=np.ones(layout.variable_count),
        bounds=Bounds(np.zeros(layout.variable_count), np.ones(layout.variable_count)),
        constraints=constraints,
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Older-coverage solve failed: {result.message}")
    coverage = int(round(-float(result.fun)))
    return coverage, result.x


def solve_tiebreak(
    pools: list[Pool],
    layout: StageTwoLayout,
    constraints: LinearConstraint,
    old_coverage: int,
    gen8_targets: list[int],
) -> np.ndarray:
    # Prefer pools with broad potential coverage after both primary objectives are fixed.
    old_set = set(layout.old_targets)
    gen8_set = set(gen8_targets)
    objective = np.zeros(layout.variable_count)
    for pool in pools:
        old_score = len(pool.covers & old_set)
        gen8_score = len(pool.covers & gen8_set)
        objective[pool.index] = -(1000 * old_score + gen8_score)

    z_row = coo_matrix(
        (
            np.ones(len(layout.old_targets)),
            (
                np.zeros(len(layout.old_targets), dtype=int),
                np.arange(layout.z_start, layout.q_start),
            ),
        ),
        shape=(1, layout.variable_count),
    ).tocsr()
    combined_matrix = vstack([constraints.A, z_row]).tocsr()
    combined = LinearConstraint(
        combined_matrix,
        np.concatenate([np.asarray(constraints.lb), [old_coverage]]),
        np.concatenate([np.asarray(constraints.ub), [old_coverage]]),
    )
    result = milp(
        c=objective,
        integrality=np.ones(layout.variable_count),
        bounds=Bounds(np.zeros(layout.variable_count), np.ones(layout.variable_count)),
        constraints=combined,
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Tie-break solve failed: {result.message}")
    return result.x


def solve_additional_pools(
    pools: list[Pool],
    remaining_targets: list[int],
    baseline_indices: set[int],
) -> np.ndarray:
    """Minimize added manipulations after the baseline coverage is frozen."""
    builder = ConstraintBuilder(len(pools))
    for target in remaining_targets:
        candidates = [
            (pool.index, 1.0)
            for pool in pools
            if pool.index not in baseline_indices and target in pool.covers
        ]
        if not candidates:
            raise ValueError(f"Older target {target} has no non-baseline covering pool")
        builder.add(candidates, lower=1.0)

    upper = np.ones(len(pools))
    for pool_index in baseline_indices:
        upper[pool_index] = 0.0
    result = milp(
        c=np.ones(len(pools)),
        integrality=np.ones(len(pools)),
        bounds=Bounds(np.zeros(len(pools)), upper),
        constraints=builder.build(),
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Additional older-pool solve failed: {result.message}")
    return result.x


@dataclass
class ExpandedLayout:
    baseline_start: int
    selected_start: int
    active_optional_start: int
    assignment_start: int
    variable_count: int
    optional_targets: list[int]
    pairs: list[tuple[int, int]]


def build_expanded_constraints(
    pools: list[Pool],
    gen8_targets: list[int],
    optional_targets: list[int],
    minimum_baseline_count: int,
    min_optional_per_pool: int,
) -> tuple[ExpandedLayout, LinearConstraint]:
    """Require a minimum Gen VIII baseline and full efficient optional coverage."""
    pairs = [
        (pool.index, target_index)
        for pool in pools
        for target_index, target in enumerate(optional_targets)
        if target in pool.covers
    ]
    pool_count = len(pools)
    layout = ExpandedLayout(
        baseline_start=0,
        selected_start=pool_count,
        active_optional_start=2 * pool_count,
        assignment_start=3 * pool_count,
        variable_count=3 * pool_count + len(pairs),
        optional_targets=optional_targets,
        pairs=pairs,
    )
    builder = ConstraintBuilder(layout.variable_count)

    # The baseline is itself an exact minimum-cardinality Gen VIII cover.
    builder.add(
        ((layout.baseline_start + pool.index, 1.0) for pool in pools),
        lower=minimum_baseline_count,
        upper=minimum_baseline_count,
    )
    for target in gen8_targets:
        builder.add(
            (
                (layout.baseline_start + pool.index, 1.0)
                for pool in pools
                if target in pool.covers
            ),
            lower=1.0,
        )

    pairs_by_target: dict[int, list[int]] = defaultdict(list)
    pairs_by_pool: dict[int, list[int]] = defaultdict(list)
    for pair_index, (pool_index, target_index) in enumerate(pairs):
        assignment_var = layout.assignment_start + pair_index
        selected_var = layout.selected_start + pool_index
        active_var = layout.active_optional_start + pool_index
        pairs_by_target[target_index].append(assignment_var)
        pairs_by_pool[pool_index].append(assignment_var)
        builder.add(((assignment_var, 1.0), (selected_var, -1.0)), upper=0.0)
        builder.add(((assignment_var, 1.0), (active_var, -1.0)), upper=0.0)

    # Every non-Gen-VIII target is assigned exactly once.
    for target_index in range(len(optional_targets)):
        builder.add(
            ((assignment_var, 1.0) for assignment_var in pairs_by_target[target_index]),
            lower=1.0,
            upper=1.0,
        )

    for pool in pools:
        baseline_var = layout.baseline_start + pool.index
        selected_var = layout.selected_start + pool.index
        active_var = layout.active_optional_start + pool.index
        builder.add(((baseline_var, 1.0), (selected_var, -1.0)), upper=0.0)
        builder.add(((active_var, 1.0), (selected_var, -1.0)), upper=0.0)
        # A selected pool must be part of the baseline, used for optional coverage, or both.
        builder.add(
            ((selected_var, 1.0), (baseline_var, -1.0), (active_var, -1.0)),
            upper=0.0,
        )
        coefficients = [(var, 1.0) for var in pairs_by_pool[pool.index]]
        coefficients.append((active_var, -float(min_optional_per_pool)))
        builder.add(coefficients, lower=0.0)

    return layout, builder.build()


def solve_expanded_plan(
    pools: list[Pool],
    layout: ExpandedLayout,
    constraints: LinearConstraint,
) -> tuple[int, np.ndarray]:
    objective = np.zeros(layout.variable_count)
    objective[layout.selected_start : layout.active_optional_start] = 1.0
    result = milp(
        c=objective,
        integrality=np.ones(layout.variable_count),
        bounds=Bounds(np.zeros(layout.variable_count), np.ones(layout.variable_count)),
        constraints=constraints,
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Expanded all-generation solve failed: {result.message}")
    return int(round(float(result.fun))), result.x


def solve_expanded_tiebreak(
    pools: list[Pool],
    layout: ExpandedLayout,
    constraints: LinearConstraint,
    selected_count: int,
    gen8_targets: list[int],
) -> np.ndarray:
    """Among minimum expanded plans, prefer pools with broader useful coverage."""
    optional_set = set(layout.optional_targets)
    gen8_set = set(gen8_targets)
    objective = np.zeros(layout.variable_count)
    for pool in pools:
        score = 1000 * len(pool.covers & optional_set) + len(pool.covers & gen8_set)
        objective[layout.selected_start + pool.index] = -float(score)

    selected_columns = np.arange(layout.selected_start, layout.active_optional_start)
    count_row = coo_matrix(
        (
            np.ones(len(selected_columns)),
            (np.zeros(len(selected_columns), dtype=int), selected_columns),
        ),
        shape=(1, layout.variable_count),
    ).tocsr()
    combined_matrix = vstack([constraints.A, count_row]).tocsr()
    combined = LinearConstraint(
        combined_matrix,
        np.concatenate([np.asarray(constraints.lb), [selected_count]]),
        np.concatenate([np.asarray(constraints.ub), [selected_count]]),
    )
    result = milp(
        c=objective,
        integrality=np.ones(layout.variable_count),
        bounds=Bounds(np.zeros(layout.variable_count), np.ones(layout.variable_count)),
        constraints=combined,
        options={"presolve": True},
    )
    if not result.success:
        raise RuntimeError(f"Expanded tie-break solve failed: {result.message}")
    return result.x


def evolution_distances(start: int, children: dict[int, set[int]]) -> dict[int, int]:
    distances = {start: 0}
    queue: deque[int] = deque([start])
    while queue:
        current = queue.popleft()
        for child in children.get(current, set()):
            new_distance = distances[current] + 1
            if child not in distances or new_distance > distances[child]:
                distances[child] = new_distance
                queue.append(child)
    return distances


def best_source(pool: Pool, target: int, children: dict[int, set[int]]) -> int:
    options: list[tuple[int, int]] = []
    for source in pool.direct_species:
        distance = evolution_distances(source, children).get(target)
        if distance is not None:
            options.append((distance, source))
    if not options:
        raise ValueError(f"Pool {pool.key} cannot produce target {target}")
    # Longest path corresponds to the earliest directly available stage.
    return max(options, key=lambda item: (item[0], -item[1]))[1]


def species_label(species_id: int, species: dict[int, dict[str, Any]]) -> str:
    name = species[species_id]["name"].replace("-", " ").title()
    return f"{name} (#{species_id})"


def build_solution(
    pools: list[Pool],
    species: dict[int, dict[str, Any]],
    children: dict[int, set[int]],
    gen8_targets: list[int],
    old_targets: list[int],
    layout: StageTwoLayout,
    values: np.ndarray,
    min_old_per_pool: int,
) -> dict[str, Any]:
    selected = sorted(
        (pool for pool in pools if values[pool.index] > 0.5),
        key=lambda pool: (
            pool.version,
            pool.beam,
            pool.tier,
            min(alias["pool_id"] for alias in pool.aliases),
            pool.key,
        ),
    )
    selected_by_index = {pool.index: pool for pool in selected}
    assignments: dict[int, dict[str, list[dict[str, Any]]]] = {
        pool.index: {"generation_8": [], "older": []} for pool in selected
    }

    # Assign mandatory targets to a selected pool that offers the earliest source stage.
    for target in gen8_targets:
        options: list[tuple[int, int, int]] = []
        for pool in selected:
            if target not in pool.covers:
                continue
            source = best_source(pool, target, children)
            distance = evolution_distances(source, children)[target]
            options.append((distance, len(pool.covers), -pool.index))
        chosen_score = max(options)
        chosen_pool = selected_by_index[-chosen_score[2]]
        source = best_source(chosen_pool, target, children)
        assignments[chosen_pool.index]["generation_8"].append(
            {"target": target, "source": source}
        )

    for pair_index, (pool_index, target_index) in enumerate(layout.pairs):
        if values[layout.a_start + pair_index] <= 0.5:
            continue
        pool = selected_by_index[pool_index]
        target = old_targets[target_index]
        assignments[pool_index]["older"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    pool_rows: list[dict[str, Any]] = []
    covered_old: set[int] = set()
    covered_gen8: set[int] = set()
    for pool in selected:
        gen8_plan = sorted(assignments[pool.index]["generation_8"], key=lambda row: row["target"])
        old_plan = sorted(assignments[pool.index]["older"], key=lambda row: row["target"])
        covered_gen8.update(row["target"] for row in gen8_plan)
        covered_old.update(row["target"] for row in old_plan)
        if old_plan and len(old_plan) < min_old_per_pool:
            raise AssertionError(f"Old-species threshold violated by {pool.key}")
        pool_rows.append(
            {
                "pool_key": pool.key,
                "version": VERSION_NAMES[pool.version],
                "beam": pool.beam,
                "progression_tier": pool.tier,
                "aliases": pool.aliases,
                "direct_species": sorted(pool.direct_species),
                "baseline_generation_8_pool": True,
                "generation_8_assignments": gen8_plan,
                "older_assignments": old_plan,
                "future_assignments": [],
                "generation_8_slot_count": len(gen8_plan),
                "older_slot_count": len(old_plan),
                "future_slot_count": 0,
                "non_generation_8_slot_count": len(old_plan),
                "non_generation_8_pool_credited": bool(old_plan),
            }
        )

    if covered_gen8 != set(gen8_targets):
        raise AssertionError("Generated plan does not assign every Generation VIII target")

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "forms_collapsed_to_species": True,
            "versions_are_separate_manipulations": True,
            "common_and_rare_are_separate_manipulations": True,
            "progression_tiers_are_separate_manipulations": True,
            "minimum_non_generation_8_slots_for_credit": min_old_per_pool,
            "optimization": "minimum Gen VIII tier-specific plan, then maximum older-generation coverage with no additional manipulations",
        },
        "summary": {
            "baseline_generation_8_manipulations": len(selected),
            "selected_manipulations": len(selected),
            "additional_manipulations": 0,
            "reachable_generation_8_species": len(gen8_targets),
            "covered_generation_8_species": len(covered_gen8),
            "reachable_older_species": len(old_targets),
            "credited_older_species": len(covered_old),
            "uncovered_reachable_older_species": len(set(old_targets) - covered_old),
            "reachable_generation_9_evolutions": 0,
            "covered_generation_9_evolutions": 0,
        },
        "generation_8_species": sorted(covered_gen8),
        "credited_older_species": sorted(covered_old),
        "uncovered_reachable_older_species": sorted(set(old_targets) - covered_old),
        "generation_9_evolutions": [],
        "selected_pools": pool_rows,
    }


def build_two_step_solution(
    pools: list[Pool],
    species: dict[int, dict[str, Any]],
    children: dict[int, set[int]],
    gen8_targets: list[int],
    old_targets: list[int],
    baseline_values: np.ndarray,
    additional_values: np.ndarray,
) -> dict[str, Any]:
    baseline = [pool for pool in pools if baseline_values[pool.index] > 0.5]
    baseline_indices = {pool.index for pool in baseline}
    additional = [
        pool
        for pool in pools
        if pool.index not in baseline_indices and additional_values[pool.index] > 0.5
    ]
    selected = sorted(
        baseline + additional,
        key=lambda pool: (
            pool.version,
            pool.beam,
            pool.tier,
            min(alias["pool_id"] for alias in pool.aliases),
            pool.key,
        ),
    )
    selected_by_index = {pool.index: pool for pool in selected}
    assignments: dict[int, dict[str, list[dict[str, Any]]]] = {
        pool.index: {"generation_8": [], "older": []} for pool in selected
    }

    def choose_pool(candidates: list[Pool], target: int) -> Pool:
        options: list[tuple[int, int, int]] = []
        for pool in candidates:
            source = best_source(pool, target, children)
            distance = evolution_distances(source, children)[target]
            options.append((distance, len(pool.covers), -pool.index))
        if not options:
            raise AssertionError(f"No selected pool covers target {target}")
        chosen = max(options)
        return selected_by_index[-chosen[2]]

    for target in gen8_targets:
        pool = choose_pool([pool for pool in baseline if target in pool.covers], target)
        assignments[pool.index]["generation_8"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    old_set = set(old_targets)
    incidental_old = set().union(*(pool.covers & old_set for pool in baseline))
    remaining_old = old_set - incidental_old

    # Every older target available in a baseline tier-pool is explicitly taken there.
    for target in sorted(incidental_old):
        pool = choose_pool([pool for pool in baseline if target in pool.covers], target)
        assignments[pool.index]["older"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    # A minimum set cover gives every added pool at least one private remaining target.
    assigned_remaining: set[int] = set()
    for pool in additional:
        private_targets = sorted(
            target
            for target in remaining_old
            if target in pool.covers
            and not any(
                target in other.covers for other in additional if other.index != pool.index
            )
        )
        if not private_targets:
            raise AssertionError(f"Added pool {pool.key} has no private older target")
        target = private_targets[0]
        assignments[pool.index]["older"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )
        assigned_remaining.add(target)

    for target in sorted(remaining_old - assigned_remaining):
        pool = choose_pool([pool for pool in additional if target in pool.covers], target)
        assignments[pool.index]["older"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    pool_rows: list[dict[str, Any]] = []
    covered_gen8: set[int] = set()
    covered_old: set[int] = set()
    for pool in selected:
        gen8_plan = sorted(assignments[pool.index]["generation_8"], key=lambda row: row["target"])
        old_plan = sorted(assignments[pool.index]["older"], key=lambda row: row["target"])
        covered_gen8.update(row["target"] for row in gen8_plan)
        covered_old.update(row["target"] for row in old_plan)
        pool_rows.append(
            {
                "pool_key": pool.key,
                "version": VERSION_NAMES[pool.version],
                "beam": pool.beam,
                "progression_tier": pool.tier,
                "aliases": pool.aliases,
                "direct_species": sorted(pool.direct_species),
                "baseline_generation_8_pool": pool.index in baseline_indices,
                "generation_8_assignments": gen8_plan,
                "older_assignments": old_plan,
                "future_assignments": [],
                "generation_8_slot_count": len(gen8_plan),
                "older_slot_count": len(old_plan),
                "future_slot_count": 0,
                "non_generation_8_slot_count": len(old_plan),
                "non_generation_8_pool_credited": bool(old_plan),
            }
        )

    if covered_gen8 != set(gen8_targets):
        raise AssertionError("Two-step plan does not assign every Generation VIII target")
    if covered_old != old_set:
        raise AssertionError("Two-step plan does not assign every older target")

    baseline_count = len(baseline)
    selected_count = len(selected)
    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "forms_collapsed_to_species": True,
            "versions_are_separate_manipulations": True,
            "common_and_rare_are_separate_manipulations": True,
            "progression_tiers_are_separate_manipulations": True,
            "manipulation_setup_minutes": 45,
            "catch_minutes_per_target": 5,
            "optimization": "step 1 minimum Gen VIII pools plus maximum incidental older coverage; step 2 minimum added setup time for remaining older targets",
        },
        "summary": {
            "baseline_generation_8_manipulations": baseline_count,
            "selected_manipulations": selected_count,
            "additional_manipulations": selected_count - baseline_count,
            "reachable_generation_8_species": len(gen8_targets),
            "covered_generation_8_species": len(covered_gen8),
            "reachable_older_species": len(old_targets),
            "credited_older_species": len(covered_old),
            "incidental_older_species_from_gen8_baseline": len(incidental_old),
            "remaining_older_species_after_baseline": len(remaining_old),
            "uncovered_reachable_older_species": 0,
            "reachable_generation_9_evolutions": 0,
            "covered_generation_9_evolutions": 0,
            "older_incremental_minutes_after_gen8": (selected_count - baseline_count) * 45 + len(covered_old) * 5,
            "total_plan_minutes": selected_count * 45 + (len(covered_gen8) + len(covered_old)) * 5,
        },
        "generation_8_species": sorted(covered_gen8),
        "credited_older_species": sorted(covered_old),
        "uncovered_reachable_older_species": [],
        "generation_9_evolutions": [],
        "selected_pools": pool_rows,
    }


def build_expanded_solution(
    pools: list[Pool],
    species: dict[int, dict[str, Any]],
    children: dict[int, set[int]],
    gen8_targets: list[int],
    older_targets: list[int],
    future_targets: list[int],
    minimum_baseline_count: int,
    layout: ExpandedLayout,
    values: np.ndarray,
    min_optional_per_pool: int,
) -> dict[str, Any]:
    selected = sorted(
        (
            pool
            for pool in pools
            if values[layout.selected_start + pool.index] > 0.5
        ),
        key=lambda pool: (
            pool.version,
            pool.beam,
            pool.tier,
            min(alias["pool_id"] for alias in pool.aliases),
            pool.key,
        ),
    )
    baseline_indices = {
        pool.index
        for pool in pools
        if values[layout.baseline_start + pool.index] > 0.5
    }
    selected_by_index = {pool.index: pool for pool in selected}
    assignments: dict[int, dict[str, list[dict[str, Any]]]] = {
        pool.index: {"generation_8": [], "older": [], "future": []}
        for pool in selected
    }

    baseline_pools = [pool for pool in selected if pool.index in baseline_indices]
    if len(baseline_pools) != minimum_baseline_count:
        raise AssertionError("Expanded plan does not preserve the minimum Gen VIII baseline")

    for target in gen8_targets:
        options: list[tuple[int, int, int]] = []
        for pool in baseline_pools:
            if target not in pool.covers:
                continue
            source = best_source(pool, target, children)
            distance = evolution_distances(source, children)[target]
            options.append((distance, len(pool.covers), -pool.index))
        if not options:
            raise AssertionError(f"Baseline does not cover Generation VIII target {target}")
        chosen = max(options)
        pool = selected_by_index[-chosen[2]]
        assignments[pool.index]["generation_8"].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    for pair_index, (pool_index, target_index) in enumerate(layout.pairs):
        if values[layout.assignment_start + pair_index] <= 0.5:
            continue
        pool = selected_by_index[pool_index]
        target = layout.optional_targets[target_index]
        bucket = "older" if species[target]["generation_id"] < 8 else "future"
        assignments[pool_index][bucket].append(
            {"target": target, "source": best_source(pool, target, children)}
        )

    covered_gen8: set[int] = set()
    covered_older: set[int] = set()
    covered_future: set[int] = set()
    pool_rows: list[dict[str, Any]] = []
    for pool in selected:
        gen8_plan = sorted(assignments[pool.index]["generation_8"], key=lambda row: row["target"])
        old_plan = sorted(assignments[pool.index]["older"], key=lambda row: row["target"])
        future_plan = sorted(assignments[pool.index]["future"], key=lambda row: row["target"])
        covered_gen8.update(row["target"] for row in gen8_plan)
        covered_older.update(row["target"] for row in old_plan)
        covered_future.update(row["target"] for row in future_plan)
        optional_count = len(old_plan) + len(future_plan)
        active_optional = bool(values[layout.active_optional_start + pool.index] > 0.5)
        if active_optional and optional_count < min_optional_per_pool:
            raise AssertionError(f"Non-Gen-VIII threshold violated by {pool.key}")
        if optional_count and not active_optional:
            raise AssertionError(f"Assignments exist on inactive optional pool {pool.key}")
        pool_rows.append(
            {
                "pool_key": pool.key,
                "version": VERSION_NAMES[pool.version],
                "beam": pool.beam,
                "progression_tier": pool.tier,
                "aliases": pool.aliases,
                "direct_species": sorted(pool.direct_species),
                "baseline_generation_8_pool": bool(pool.index in baseline_indices),
                "generation_8_assignments": gen8_plan,
                "older_assignments": old_plan,
                "future_assignments": future_plan,
                "generation_8_slot_count": len(gen8_plan),
                "older_slot_count": len(old_plan),
                "future_slot_count": len(future_plan),
                "non_generation_8_slot_count": optional_count,
                "non_generation_8_pool_credited": active_optional,
            }
        )

    if covered_gen8 != set(gen8_targets):
        raise AssertionError("Expanded plan does not assign every Generation VIII target")
    if covered_older != set(older_targets):
        raise AssertionError("Expanded plan does not assign every older target")
    if covered_future != set(future_targets):
        raise AssertionError("Expanded plan does not assign every future evolution")

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "forms_collapsed_to_species": True,
            "versions_are_separate_manipulations": True,
            "common_and_rare_are_separate_manipulations": True,
            "progression_tiers_are_separate_manipulations": True,
            "minimum_non_generation_8_slots_for_credit": min_optional_per_pool,
            "manipulation_setup_minutes": 45,
            "catch_minutes_per_target": 5,
            "optimization": "minimum Gen VIII baseline, then minimum-time expanded plan for every reachable older-generation species",
        },
        "summary": {
            "baseline_generation_8_manipulations": minimum_baseline_count,
            "selected_manipulations": len(selected),
            "additional_manipulations": len(selected) - minimum_baseline_count,
            "reachable_generation_8_species": len(gen8_targets),
            "covered_generation_8_species": len(covered_gen8),
            "reachable_older_species": len(older_targets),
            "credited_older_species": len(covered_older),
            "uncovered_reachable_older_species": 0,
            "reachable_generation_9_evolutions": len(future_targets),
            "covered_generation_9_evolutions": len(covered_future),
            "older_incremental_minutes_after_gen8": (len(selected) - minimum_baseline_count) * 45 + len(covered_older) * 5,
            "total_plan_minutes": len(selected) * 45 + (len(covered_gen8) + len(covered_older)) * 5,
        },
        "generation_8_species": sorted(covered_gen8),
        "credited_older_species": sorted(covered_older),
        "generation_9_evolutions": sorted(covered_future),
        "selected_pools": pool_rows,
    }


def write_outputs(
    output_dir: Path,
    solution: dict[str, Any],
    species: dict[int, dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "coverage_solution.json"
    csv_path = output_dir / "coverage_solution.csv"
    report_path = output_dir / "coverage_report.md"
    json_path.write_text(json.dumps(solution, indent=2) + "\n", encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "pool_key",
                "version",
                "beam",
                "progression_tier",
                "pool_ids",
                "baseline_gen8_pool",
                "generation",
                "source_species",
                "target_species",
            ],
        )
        writer.writeheader()
        for pool in solution["selected_pools"]:
            pool_ids = ";".join(str(alias["pool_id"]) for alias in pool["aliases"])
            for generation, field in (
                ("8", "generation_8_assignments"),
                ("1-7", "older_assignments"),
                ("9", "future_assignments"),
            ):
                for assignment in pool[field]:
                    writer.writerow(
                        {
                            "pool_key": pool["pool_key"],
                            "version": pool["version"],
                            "beam": pool["beam"],
                            "progression_tier": pool["progression_tier"],
                            "pool_ids": pool_ids,
                            "baseline_gen8_pool": pool["baseline_generation_8_pool"],
                            "generation": generation,
                            "source_species": species_label(assignment["source"], species),
                            "target_species": species_label(assignment["target"], species),
                        }
                    )

    summary = solution["summary"]
    lines = [
        "# Sword/Shield minimum-manipulation raid coverage solution",
        "",
        f"- Minimum Generation VIII baseline: **{summary['baseline_generation_8_manipulations']} manipulations**",
        f"- Selected plan: **{summary['selected_manipulations']} manipulations**",
        f"- Reachable Generation VIII coverage: **{summary['covered_generation_8_species']}/{summary['reachable_generation_8_species']}**",
        f"- Credited older-generation coverage: **{summary['credited_older_species']}/{summary['reachable_older_species']}**",
        f"- Incidental older targets assigned to the Gen VIII baseline: **{summary.get('incidental_older_species_from_gen8_baseline', 0)}**",
        f"- Older targets left for the second run: **{summary.get('remaining_older_species_after_baseline', summary['reachable_older_species'])}**",
        f"- Uncovered reachable older species: **{summary.get('uncovered_reachable_older_species', 0)}**",
        f"- Incremental older-generation time after the Gen VIII baseline: **{summary.get('older_incremental_minutes_after_gen8', 0)} minutes**",
        f"- Total modeled raid-plan time: **{summary.get('total_plan_minutes', 0)} minutes**",
        "",
        "Sword/Shield, common/rare beams, and early/late progression tiers are separate manipulations. Regional and Gigantamax forms are collapsed to species. The plan first minimizes manipulations covering Generation VIII, then maximizes credited older-generation coverage without adding manipulations.",
        "",
        "## Selected pools",
        "",
    ]
    for pool in solution["selected_pools"]:
        aliases = ", ".join(
            f"pool {alias['pool_id']} (table index {alias['raid_table_index']})"
            for alias in pool["aliases"]
        )
        direct = ", ".join(species_label(value, species) for value in pool["direct_species"])
        lines.extend(
            [
                f"### {pool['pool_key']}",
                "",
                f"- Version/beam: {pool['version'].title()} {pool['beam']}",
                f"- Progression tier: {pool['progression_tier']} ({'1-2 stars' if pool['progression_tier'] == 'early' else '3-5 stars'})",
                f"- Aliases: {aliases}",
                f"- Part of minimum Gen VIII baseline: {'yes' if pool['baseline_generation_8_pool'] else 'no'}",
                f"- Direct raid species: {direct}",
                f"- Assigned Generation VIII slots: {pool['generation_8_slot_count']}",
                f"- Assigned older slots: {pool['older_slot_count']}",
                f"- Assigned Generation IX evolutions: {pool['future_slot_count']}",
                "",
            ]
        )
        plans = (
            pool["generation_8_assignments"]
            + pool["older_assignments"]
            + pool["future_assignments"]
        )
        for assignment in plans:
            source = species_label(assignment["source"], species)
            target = species_label(assignment["target"], species)
            action = "catch directly" if assignment["source"] == assignment["target"] else f"evolve to {target}"
            lines.append(f"- {source}: {action}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.min_old_per_pool < 1:
        raise ValueError("--min-old-per-pool must be at least 1")
    root = args.project_root.resolve()
    reference = load_reference(args.reference.resolve(), args.refresh_reference)
    raid_tables = load_json(root / "data" / "raid_tables.json")
    dens = load_json(root / "data" / "den_locations.json")
    early_access = load_json(root / "data" / "early_inaccessible_dens.json")
    early_inaccessible_dens = {int(value) for value in early_access["dens"]}
    known_den_numbers = {int(den["den_number"]) for den in dens}
    unknown_flags = early_inaccessible_dens - known_den_numbers
    if unknown_flags:
        raise ValueError(f"Unknown early-inaccessible den numbers: {sorted(unknown_flags)}")
    pools = build_pools(raid_tables, dens, early_inaccessible_dens)
    species, children = build_species_graph(reference)
    descendants = descendant_map(species, children)
    attach_coverage(pools, descendants)

    reachable = set().union(*(pool.covers for pool in pools))
    gen8_targets = sorted(value for value in reachable if species[value]["generation_id"] == 8)
    old_targets = sorted(value for value in reachable if species[value]["generation_id"] < 8)
    print(
        f"Candidates: {len(pools)} version-specific beam pools; "
        f"targets: {len(gen8_targets)} Gen VIII and {len(old_targets)} older; "
        f"{len(early_inaccessible_dens)} dens flagged early-inaccessible",
        flush=True,
    )

    minimum_pool_count, _ = solve_stage_one(pools, gen8_targets)
    print(f"Stage 1: minimum manipulations = {minimum_pool_count}", flush=True)
    baseline_layout, baseline_constraints = build_stage_two_constraints(
        pools,
        gen8_targets,
        old_targets,
        minimum_pool_count,
        1,
    )
    incidental_count, _ = solve_stage_two(
        pools, baseline_layout, baseline_constraints
    )
    baseline_values = solve_tiebreak(
        pools,
        baseline_layout,
        baseline_constraints,
        incidental_count,
        gen8_targets,
    )
    baseline_indices = {
        pool.index for pool in pools if baseline_values[pool.index] > 0.5
    }
    old_set = set(old_targets)
    incidental_old = set().union(
        *(pool.covers & old_set for pool in pools if pool.index in baseline_indices)
    )
    if len(incidental_old) != incidental_count:
        raise AssertionError("Baseline incidental coverage differs from optimized coverage")
    remaining_old = sorted(old_set - incidental_old)
    print(
        f"Stage 1b: incidental older coverage = {len(incidental_old)}/{len(old_targets)}; "
        f"remaining older targets = {len(remaining_old)}",
        flush=True,
    )
    additional_values = solve_additional_pools(
        pools, remaining_old, baseline_indices
    )
    additional_count = int(round(float(additional_values.sum())))
    print(
        f"Stage 2: minimum additional older-only manipulations = {additional_count}; "
        f"comprehensive total = {minimum_pool_count + additional_count}",
        flush=True,
    )
    solution = build_two_step_solution(
        pools,
        species,
        children,
        gen8_targets,
        old_targets,
        baseline_values,
        additional_values,
    )
    write_outputs(args.output_dir.resolve(), solution, species)
    print(f"Wrote results to {args.output_dir.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # concise CLI failure while retaining traceback in development
        print(f"error: {exc}", file=sys.stderr)
        raise
