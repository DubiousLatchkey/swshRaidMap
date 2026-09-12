# Sword/Shield raid-den numbering survey

Survey date: 2026-09-11

## Executive finding

There is no single community "den number." Public tools use numbers for two different entities:

1. **Physical den / raid-save slot**: one place on a map. The game-data-derived order contains 276 slots: 100 Wild Area slots, 90 Isle of Armor slots, and 86 Crown Tundra slots. Wild Area slot 16 (zero-based), the Watchtower Lair crystal den, is special and is normally omitted from ordinary raid selection, leaving 275 ordinary selectable dens.
2. **Encounter pool / nest table**: the table selected by a normal red beam or rare purple beam. The familiar Serebii labels run 1-197, and one physical den points to a common pool and a rare pool. Many physical dens reuse the same pool.

Numbers from these two namespaces are not interchangeable. The 64-bit normal/rare table hashes are the safest cross-project join keys. A durable unified record should key a physical den by region plus game/save slot, and key a pool by table hash plus game version; all human-facing numbers should be aliases.

## Numbering families

| Family | What the number identifies | Range / offset | Common vs rare | Projects and sites found | Consistency |
|---|---|---|---|---|---|
| Game/save-array index | Physical den slot | Global zero-based `0..275`; boundaries `0`, `100`, `190`; includes special WA index 16 | Rarity is a separate field and selects one of two hashes | PKHeX internals, current PokéFinder internals, pkTeraRaid, CaptureSight-derived code, minidex, pokefinder_rs | Same underlying order where copied from the game/Leanny data |
| Leanny / Seed Searcher UI | Physical den | One-based within each selector: WA `1..16, 18..100`; IoA `1..90`; TC `1..80`. The six Slippery Slope slots are omitted. Event den is displayed separately. | Separate Normal/Rare selector | SeedSearcher, PKHeX Raid Plugin, RaidFinder, PokéFinder UI, Seed Checker web den overview | Same physical order; labels differ from the zero-based internal index by region offset and +1 |
| PKHeX Raids display documented in 2020 | Physical den | Zero-based, padded (`Den 003` = Leanny `Rolling Fields 3`, i.e. Leanny physical den 3) | Separate raid type | PKHeX and Project Pokémon documentation | Same order as Leanny, but zero-based display creates a one-off mismatch |
| Serebii pool number | Encounter pool, not a physical den | Base pools `1..93`; Isle of Armor pools extend through `157` (observed IoA normal/rare pairs include `99/100`); Crown Tundra pools `158..197` | Normal and rare have different numbers | Serebii Max Raid pages and Pokéarth location pages | Consistent as a pool-label system, but a physical location therefore has two "Den" numbers |
| Serebii-derived map family | Encounter pool attached to a map point | Same pool numbers as Serebii | Usually shown as red/common and purple/rare | pokedens.github.io, cecilbowen map, Trainer_A maps, Poké Atlas, numerous static Reddit maps/guides | Generally aliases Serebii rather than defining a new physical numbering system |
| Game8 / Japanese-wiki map family | Physical location ordinal within each named subarea | Restarts in each subarea (for example, the nth den shown for that area) | Separate normal and rare tables | Game8 maps and early translated Reddit maps | Not globally comparable by number alone; `(subarea, local ordinal)` is required |
| This repository | Physical den, program-compatible | Region-scoped `wa-###`, `ioa-###`, `ct-###`, and `ss-###`; CT follows SeedSearcher TC numbering and SS is the six-slot game-table area omitted by SeedSearcher | `common_pool`, `rare_pool`, `common_hash`, and `rare_hash` stored on each point | swshRaidMap | Physical IDs follow the generated SeedSearcher/PokéFinder crosswalk; pool numbers remain encounter-pool aliases. |

## Exact Seed Searcher behavior

Seed Searcher exposes both namespaces in the same form:

- **Den** chooses a physical location. Its source constructs Wild Area labels with the special Watchtower slot skipped, then labels DLC regions separately as `IoA 1..90` and `TC 1..80`; the six `DLC_32` Slippery Slope slots are not in the selector.
- **Rarity** chooses Normal or Rare.
- The selected physical den and rarity yield a 64-bit table hash from `NestLocations.Nests`.
- Seed Searcher finds the matching raid table by that hash and updates **Nest**.
- **Nest** is the encounter-table selector. Its UI lists `1..197`, with `Event` separately; its import/export `Nest ID` is a zero-based array index, not a physical den identifier.

This means a Seed Searcher integration needs both a physical-den alias and the normal/rare table hash. Merely renumbering the map to 1-197 would destroy the physical-location identity.

## Seed Searcher versus PokéFinder verification

Verified on 2026-09-11 against:

- SeedSearcher commit `e4cb8c34752d68b63f1202d69baf4ae260a51f88` (2023-01-14), including reflection over its bundled `PKHeX_Raid_Plugin.dll`;
- PokéFinder commit `f81cb352db941b215a7a09a9d792c77f86f8d14e` (2026-09-10), using `Core/Gen8/Encounters8.cpp` and the current Gen 8 raid/den-map UI code.

The physical numbering lines up. Both contain exactly 276 physical-table rows in the same order. A row-by-row comparison found:

| Compared field | Matching rows | Mismatching rows |
|---|---:|---:|
| Common table hash | 276 | 0 |
| Rare table hash | 276 | 0 |
| Location/subarea ID | 276 | 0 |
| Array position | 276 | 0 |
| Map X coordinate | 190 | 86 |
| Map Y coordinate | 190 | 86 |

All coordinate differences are the Crown Tundra-area rows `190..275`: Seed Searcher's bundled dependency stores `0,0`, while current PokéFinder supplies coordinates. The identifiers and ordering still match exactly; rows `190..195` are the six Slippery Slope slots omitted from the SeedSearcher selector.

The user-facing conversion is therefore deterministic for rows exposed by
SeedSearcher:

```text
global index 0..16     -> Wild Area display number = index + 1
global index 17        -> Watchtower special slot (skipped by ordinary selector)
global index 18..99    -> Wild Area display number = index + 1
global index 100..189  -> Isle of Armor number      = index - 99
global index 190..195  -> Slippery Slope slot      = app ss-001..ss-006 (omitted by SeedSearcher)
global index 196..275  -> Crown Tundra number     = index - 195
```

There is one UI caveat. Global index `16` / Wild Area display number `17` is the special Watchtower Lair crystal slot with zero normal and rare hashes. Seed Searcher's ordinary Den list and PokéFinder's raid selector both skip it. PokéFinder's standalone Den Map includes it because that map displays all 100 physical Wild Area slots. Thus the ordinary selectable labels are:

```text
Wild Area:       1..16, 18..100 (99 ordinary dens)
Isle of Armor:   1..90            (90 ordinary dens)
Slippery Slope:  app ss-001..006 (6 game-table slots, not in SeedSearcher)
Crown Tundra:   1..80            (80 SeedSearcher TC entries)
```

Seed Searcher's displayed `0: Event Den` is not physical slot zero; it is a separate event-table choice.

## Pool identity caveat

The displayed Serebii/Seed Searcher pool label is not a sufficient database primary key in the current extracted data. In this repository, pool labels 103, 104, 117, and 118 each occur twice per game version with different encounter signatures. Null pool labels also occur in source data. Consequently:

- join by 64-bit table hash whenever it is available;
- include Sword/Shield version when identifying encounter content;
- preserve a content signature as a verification field;
- treat the 1-197 number as a display alias only.

## Project/repository inventory

### Inspected and classifiable

- [Leanny/SeedSearcher](https://github.com/Leanny/SeedSearcher): physical Den + rarity -> hash -> Nest table. UI source explicitly applies the `100` and `190` DLC offsets and skips the special Wild Area slot.
- [Leanny/PKHeX_Raid_Plugin](https://github.com/Leanny/PKHeX_Raid_Plugin): source of the 276-entry physical-location/hash table and area labels used by Seed Searcher.
- [Admiral-Fish/RaidFinder](https://github.com/Admiral-Fish/RaidFinder): uses the same physical ordering; its UI is one-based within regions and skips physical slot 16 in ordinary raid selection. Its older Den Map covered WA and IoA.
- [Admiral-Fish/PokeFinder](https://github.com/Admiral-Fish/PokeFinder): current successor; same 276-entry order and one-based-per-region UI, with WA/IoA/CT offsets 0/100/190.
- [Insektaure/pkTeraRaid](https://github.com/Insektaure/pkTeraRaid): generated directly from PKHeX Raid Plugin; explicitly exposes 276 zero-based entries internally.
- [rusted-coil/OneStar](https://github.com/rusted-coil/OneStar): seed-search implementation using nest/table IDs; an ancestor of RaidFinder rather than a new mapped physical numbering system.
- [pokedens/pokedens.github.io](https://github.com/pokedens/pokedens.github.io): base-game map whose data calls the two Serebii values `commonPool` and `rarePool`; no independent physical numeric ID is presented to users.
- [cecilbowen/pokemon-swsh-galar-dens](https://github.com/cecilbowen/pokemon-swsh-galar-dens): base-game Leaflet map linking numeric values directly to Serebii `denN.shtml`, so its numbers are Serebii pool IDs.
- [foohyfooh/PKHeXPluginPile](https://github.com/foohyfooh/PKHeXPluginPile): carries an unmaintained copy of the Leanny physical/hash scheme.
- [SteveCookTU/pokefinder_rs](https://github.com/SteveCookTU/pokefinder_rs): Rust port containing the same hash-led den loader family.
- [marcrobledo/minidex](https://github.com/marcrobledo/minidex): contains the same table hashes; it does not establish a conflicting mapped physical-number standard.
- [969981/PokeFinder](https://github.com/969981/PokeFinder): fork/older branch of the PokéFinder/RaidFinder family, not an independent namespace.

### Public maps/guides found but not fully machine-inspectable

- [Serebii Max Raid Battle Dens](https://www.serebii.net/swordshield/maxraidbattledens.shtml) and its Pokéarth area pages: authoritative source for the community pool labels, but labels pools as "Den."
- [Project Pokémon Max Raid Parameters](https://projectpokemon.org/home/docs/gen-8_156/max-raid-parameters-r124/): uniquely useful crosswalk showing PKHeX's zero-based physical den display, Leanny's area-local physical name, and the two Serebii pool numbers.
- [Poké Atlas](https://www.pokeatlas.com/about/): covers all three regions and appears to present mapped raid/pool information. Its implementation/data are not public, so every numeric alias must be verified against hashes/content before import.
- Game8 and translated early maps: use local ordering within named areas. Current pages are editorial/image-driven and are not a stable machine-readable canonical source.
- Trainer_A and other Reddit image maps: generally state that they cross-reference Serebii and label rare/common pool numbers. They are useful visual evidence, not a separate canonical identifier set.
- [Billo's current SwSh RNG guide](https://billo-guides.github.io/retail/swsh/raid): explicitly tells users to use PokéFinder's physical Den ID and Serebii for encounter tables, confirming the two-namespace distinction.

### Not separate numbering schemes

CaptureSight, raid bots/auto-hosters, and event-den archives often expose a save-slot index, a table hash, a seed, or a Serebii pool label. Unless they publish an independent location map/order, these are consumers of one of the families above rather than additional numbering systems. Event/promoted dens are also not physical-location numbering systems: the active Wild Area News table can override the normal ROM encounter table at many physical dens.

## Consistency verdict

- The **physical ordering** is internally consistent across Leanny's projects, RaidFinder/PokéFinder, and projects derived from their table. Differences are presentation: zero-based global index versus one-based per-region number, plus whether the Watchtower special slot is shown.
- The **Serebii numbering** is internally consistent across Serebii-derived web maps, but it identifies encounter pools. It is intentionally many-to-many with physical dens and must not be compared numerically to Leanny/PokéFinder den IDs.
- **Game8/local-area ordinals** can be made consistent only when paired with the subarea name and verified spatially.
- **swshRaidMap's physical numbering is a different permutation**. Its pool fields largely use the Serebii namespace, while its hashes provide the reliable bridge to Seed Searcher/PokéFinder.

## Recommended canonical model for the later integration

Use immutable identities and retain every numbering system as aliases:

```text
physical_den:
  region                 # wild_area | isle_of_armor | crown_tundra
  game_slot_index        # 0..275 global, canonical physical join
  region_den_number      # WA 1..16,18..100 / IoA 1..90 / CT 1..80; null for SS slots
  swsh_raid_map_number   # current 1..275 alias
  subarea
  subarea_ordinal
  map coordinates/assets
  is_special_crystal_den

den_pool_edge:
  physical_den
  beam                    # common | rare
  table_hash              # canonical table join
  serebii_pool_number     # display alias, nullable/non-unique

encounter_table:
  table_hash
  game_version
  content_signature
  encounters
```

Before contributing location data to SeedSearcher, generate and test a complete 275-row crosswalk. Match exact `(common_hash, rare_hash)` first, then disambiguate repeated hash pairs with region/subarea and coordinates. Do not infer mappings from numeric proximity.

## Discovery limits

The survey used web search, GitHub repository search, GitHub code search for known table hashes and 276-entry constants, source inspection, and cross-checks against this repository. It covers every distinct publicly discoverable scheme found by those methods, not every private Discord spreadsheet, deleted site, unindexed image, or closed-source bot. Those inaccessible artifacts cannot responsibly be claimed as verified numbering systems.
