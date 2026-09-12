# Project guidance

## Purpose

This is a static Sword/Shield raid-map app intended to support shiny Pokédex completion.

## Data model

- `data/den_locations.json` is the hand-maintained legacy source, with arbitrary physical den numbers, common/rare pool IDs, hashes, and map coordinates.
- `data/den_locations_pokefinder.json` is the generated app data source with SeedSearcher/PokéFinder physical IDs, region numbers, and location-local labels. Do not expose or copy the former arbitrary den numbers into it.
- `data/reference/pokefinder_den_locations.json` is the pinned 276-slot program reference, including the special Watchtower crystal slot; rebuild both generated files with `scripts/build_pokefinder_den_locations.py`.
- `data/early_inaccessible_dens.json` lists water- or island-gated dens that cannot contribute 1-2 star coverage; unlisted and explicitly borderline dens default to early-accessible.
- `data/raid_tables.json` contains versioned encounter tables (`version: 1` is Sword, `version: 2` is Shield).
- Encounter identity is not just National Pokédex species: preserve `altForm` and `gigantamax` when relevant.
- `data/pokemon_names.json` maps National Pokédex IDs to display names.

## Known data caveat

Do not assume `(pool_id, version)` is unique. Pool IDs 103, 104, 117, and 118 each occur twice per version with different encounters, and null pool IDs also occur. The optimizer preserves explicit `raid_table_index` aliases, while the UI resolves mapped dens with the composite `(pool_id, source_hash)` crosswalk validated against Serebii; never fall back to a global hash-only or first-match lookup for these duplicate IDs.

## Development

- The app has no build step; serve the repository over HTTP when testing browser behavior because it fetches local JSON files.
- Keep generated/reference data separate from the hand-maintained raid inputs and record its source and retrieval timestamp.
- Preserve the untracked `images/map_ioa.afphoto` source file.
- Den image folders are named by region-scoped physical ID (`wa-###`, `ioa-###`, `ct-###`, or `ss-###` for Slippery Slope), never by encounter-pool number. Crown Tundra `ct-###` IDs follow SeedSearcher's TC numbering; the six omitted Slippery Slope slots use `ss-001` through `ss-006`.
- The map's unified search parses Pokémon names/IDs, den numbers, regular pool numbers, and selected `pool_key` entries from `output/raid_coverage/coverage_solution.json`; it accepts key chunks, star ranges, pool IDs, signatures, and covered Pokémon names, uses output aliases and den source hashes to resolve duplicate pool IDs, and excludes early-inaccessible dens from `early` matches. When an optimization pool key is active, matching den popups highlight the selected version/beam's encounters in that pool's star tier.
- Map search separates `Dens` and `Hunts`. Hunt suggestions and workbook Hunt Name fields use copy/pasteable `version-beam-tier-pool_id` keys such as `sword-common-early-9`; raw 12-character solution hashes remain internal compatibility aliases.
- Physical-location search accepts canonical IDs (`wa-003`, `ioa-012`, `ct-004`, `ss-004`), SeedSearcher `tc` aliases for Crown Tundra, full region plus program number, and location-local labels such as `Rolling Fields 3`. The map uses uncluttered point markers without permanent text labels.
- On mobile, the page uses a dynamic-height app shell with a compact title bar, a map that fills the remaining viewport, and an internally scrollable filter panel; the document itself should not become the scrolling surface. Short landscape viewports move the filter panel to the top-right so it remains reachable above the fold.

## Coverage optimizer

- `scripts/optimize_raid_coverage.py` uses PokéAPI's species-level evolution graph and SciPy MILP.
- The optimization unit is a version-specific, beam-specific, progression-tier-specific encounter signature. Sword/Shield, common/rare, and early 1-2 star/late 3-5 star tiers each require separate manipulations.
- Regional, alternate, and Gigantamax forms collapse to National Pokédex species for coverage.
- Living-dex coverage is forward-only through evolution, with repeated catches allowed.
- Generation VIII coverage is mandatory and limited to reachable species, including later Generation VIII evolutions reachable from raid catches. The solver first finds an exact-minimum Gen VIII baseline and maximizes/assigns every incidental older target within those exact star-tier pools. It freezes that baseline, removes its older targets, then minimizes added 45-minute pool setups covering only the remainder.
- Required pools serving only one or two older living-dex targets remain eligible and are retained in the output as reference coverage.
- Generated results live under `output/raid_coverage/`; the normalized PokéAPI cache lives under `data/reference/`.
- The current tier-aware comprehensive solution has a 25-manipulation Gen VIII baseline and 94 total manipulations covering all 73 reachable Gen VIII and all 470 reachable Generation I-VII targets. It assigns 180 incidental older targets to baseline pools, leaves 290 for 69 added pools, allows one- or two-target added pools, and models 5,455 incremental older-target minutes / 6,945 total raid-plan minutes.
