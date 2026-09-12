# Program-compatible den location data

`data/den_locations_pokefinder.json` is the map's generated, program-compatible
location source. It preserves the hand-placed map coordinates and pool labels
from `data/den_locations.json`, while using the physical ordering shared by
SeedSearcher and PokéFinder.

## Physical identity and labels

Each ordinary den has a stable, region-scoped `physical_id`:

- `wa-001` through `wa-100`, except special Watchtower slot `wa-017`;
- `ioa-001` through `ioa-090`;
- `ct-001` through `ct-080` for the SeedSearcher Crown Tundra sequence;
- `ss-001` through `ss-006` for Slippery Slope, whose six slots are present in
  the shared game table but omitted from SeedSearcher's Crown Tundra selector.

`program_den_number` is the number shown by SeedSearcher/PokéFinder within that
region. `location_name` and `local_number` form the familiar local label, such
as `Rolling Fields 3`, also stored as `display_name`. The former arbitrary map
number is not included. Common and rare pool numbers remain display aliases; their 64-bit
hashes are the durable encounter-table joins.

SeedSearcher/PokéFinder program IDs are the authoritative physical-ID namespace
used by the map. Serebii's den-page ordering and displayed den numbers are used
to verify encounter pools, but are not a replacement for those program IDs;
the two sources do not use the same within-region ordering.

The source reference at `data/reference/pokefinder_den_locations.json` contains
all 276 game slots, including the special Watchtower crystal slot. The main data
source intentionally contains the 275 ordinary selectable dens represented on
the maps.

## Rebuilding and validating the crosswalk

From the repository root:

```powershell
python scripts/build_pokefinder_den_locations.py
```

Rows are first joined by `(location, common_hash, rare_hash)`. When multiple
physical locations share that exact tuple, a minimum-distance assignment in the
corresponding map region resolves the otherwise indistinguishable records. This
is why pool number—or even both pool hashes—must not be used as a physical den
identifier.

## Image folders

Image folders use `physical_id`, so two map locations sharing encounter pools
still have independent assets.
