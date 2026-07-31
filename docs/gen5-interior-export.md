# Gen 5 interior export

RAE treats an interior as exact Nitro terrain geometry plus authoring metadata. It does not flatten, redraw, or hand-assemble the source model.

For a carved Black 2 map model, open **Map Objects → Export…**. When the terrain contains both floor and wall materials, two additional actions appear:

- **Interior shell + grid metadata** writes `map_NNNN_interior.glb` and `map_NNNN_interior.interior.json`.
- **Interior kit pieces** also writes `kit/interior-kit.zip`, its manifest, and reusable GLBs grouped by source material role.

The sidecar records the ROM path/index, AreaData index, exact source bounds, grid origin, walkable/blocked masks, inferred floor-height step, per-cell heights, material roles, and entrance/halo coordinates. The same object is embedded at `extras.rae.interiorMap` in the GLB.

Kit parts retain their source vertex buffers, UVs, embedded textures, and applicable material animation metadata. Each part embeds `extras.rae.interiorKitPart`; `interior-kit.json` records its reversible placement, footprint, collision hint, and source material. The ZIP is the Map Studio import artifact.

## Vertically overlapping floors

Pokémon Resort's OWMAP terrain is a 2D grid with one gameplay height per cell. RAE records every vertically ambiguous cell in `ambiguousFloorCells` and chooses the highest sampled surface for `heightMask`. A stacked floor is therefore preserved visually in the shell but must be split into separate Resort maps/spaces before its lower and upper levels can both be playable.

