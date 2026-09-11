# swshRaidMap
Made for shiny dex completion.  Allows quick filtering of raids by species displaying stars and version.

My intention was to combine the best parts of Pokedens (den details and filtering) with the best parts of Poke Atlas (immersive maps and accurate positioning) and then add onto it.


# License and Attribution 
Inspired by pokedens.github.io, data taken from pkhex raid plugin.  Reference den locations taken from Poke Atlas.  Wild area map image from Bulbapedia.

Obviously I don't own Pokemon, but anything here that's copywritable I dedicate to the public domain.

## Raid coverage optimizer

`scripts/optimize_raid_coverage.py` runs a frozen two-step optimization. First it finds the minimum version/beam/progression-tier manipulations covering Generation VIII and, among exact-minimum baselines, maximizes incidental Generation I-VII coverage; every older target available within those selected star tiers is assigned there. It then removes those incidental targets and minimizes added pool manipulations covering only the remaining older targets. With fixed five-minute catch costs, minimizing added 45-minute setups is equivalent to minimizing total time.

The optimizer treats Sword/Shield, common/rare beams, and early (1-2 star)/late (3-5 star) progression tiers as separate manipulations. `data/early_inaccessible_dens.json` flags water- or island-gated physical dens that cannot contribute an early-tier table; unlisted and borderline dens default to early-accessible. Pokémon can be caught repeatedly and evolved forward; forms and Gigantamax factor collapse to National Pokédex species. Required pools serving only one or two older targets are retained.

Run it from the repository root:

```powershell
python scripts/optimize_raid_coverage.py
```

The first run downloads and caches a normalized PokéAPI species/evolution reference through Generation IX in `data/reference/`. Use `--refresh-reference` to update it. Results are written to `output/raid_coverage/` as JSON, CSV, and Markdown.
