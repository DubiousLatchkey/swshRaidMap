# Program-compatible den location data

`data/den_locations_pokefinder.json` is the map's generated, program-compatible
location source. It preserves the hand-placed map coordinates and pool labels
from `data/den_locations.json`, while adding the physical ordering shared by
SeedSearcher and PokéFinder.

## Physical identity and labels

Each ordinary den has a stable, region-scoped `physical_id`:

- `wa-001` through `wa-100`, except special Watchtower slot `wa-017`;
- `ioa-001` through `ioa-090`;
- `ct-001` through `ct-086`.

`program_den_number` is the number shown by SeedSearcher/PokéFinder within that
region. `location_name` and `local_number` form the familiar local label, such
as `Rolling Fields 3`, also stored as `display_name`. `legacy_den_number` records
this project's former `1..275` folder/map number and is retained only as an
alias. The common and rare pool numbers remain display aliases; their 64-bit
hashes are the durable encounter-table joins.

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
still have independent assets. The migration is dry-run by default:

```powershell
python scripts/rename_den_image_folders.py
python scripts/rename_den_image_folders.py --apply
```

To restore the legacy numeric names, run the same command with `--reverse`
(dry-run first, then add `--apply`). The script requires a complete 275-row
bijection, rejects missing sources and target collisions, and stages every
folder before assigning final names.
