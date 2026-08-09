# Pokémon Black 2 Models Resource map export

The exporter groups Nintendo DS map cells using the ROM's matrix topology and
official English location-name bank. Adjacent cells in one continuous matrix
component are stitched into one submission. A loading transition to another
matrix or zone creates another submission, so separate floors, rooms, caves,
and facility sections are never hidden as extra DAE scenes in one ZIP. Cave
matrices are the conservative exception: reciprocal horizontal portals can
join same-floor sections using their authored warp coordinates. Stair/lift
transitions, conflicting portals, and overlapping/nonlinear layouts remain
independent submissions.

Generate the catalog first:

```bash
cd /Users/vanta/Desktop/title_screen_demo/rae
.venv/bin/python scripts/export_black2_models_resource_maps.py --catalog-only
```

This writes `exports/models_resource/pokemon_black_2/map_review.csv`. Outdoor
names are official ROM names. Interior rows are deliberately marked for review:
edit `title`, set `include` to `no` to skip one, or change `section` before the
full export. The command preserves an existing review CSV.

Export everything after review:

```bash
.venv/bin/python scripts/export_black2_models_resource_maps.py
```

Focused/resumable examples:

```bash
.venv/bin/python scripts/export_black2_models_resource_maps.py --section maps
.venv/bin/python scripts/export_black2_models_resource_maps.py --section interiors --limit 10
.venv/bin/python scripts/export_black2_models_resource_maps.py --start-index 51
.venv/bin/python scripts/export_black2_models_resource_maps.py --images-only
.venv/bin/python scripts/export_black2_models_resource_maps.py --fresh
.venv/bin/python scripts/export_black2_models_resource_maps.py --force
```

Complete packages are skipped unless `--force` is supplied. Output follows the
uploader's four-file contract under `Maps/Locations/Name/` and
`Interior Maps/Locations/Name/`. Each DAE is re-imported by Assimp before it is
packaged; byte-identical PNG textures are deduplicated. GLB previews retain map
and placed-object animations, while DAE files use the stable default pose for
maximum COLLADA compatibility.

A complete `--force` run removes the selected generated `Maps` and/or
`Interior Maps` folders before rebuilding them. It does not make a backup;
use `--fresh` when an archived copy of the previous packages is wanted.

Map icons use a front-biased 12-degree yaw and 35-degree camera height. This
adds visible side depth while retaining a recognizable frontal composition.
