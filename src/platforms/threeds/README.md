# 3DS platform (active)

Nintendo 3DS support for **decrypted** dumps (`.3ds`, `.cci`, `.cxi`). RAE never
ships or applies AES keys — encrypted images are detected and reported.

Verified against legal *Pokémon Ultra Moon* and *LBX: Little Battlers
eXperience* cartridge dumps (NoCrypto `.cci`).

## Pipeline

- `container.py` — NCSD/NCCH headers, RomFS (IVFC level 3) tree, GARC v4/v6 tables.
- `lz11.py` — platform-local LZ11 decompression.
- `pica.py` — PICA200 command-stream reader + texture decode (swizzled 8×8
  Morton tiles; RGBA8/RGB8/RGBA5551/RGB565/RGBA4/LA8/L8/A8/LA4/L4/A4/HILO8/ETC1/ETC1A4).
- `gf.py` — Game Freak GFModel/GFTexture parsing (bones, materials with texture
  units + UV transforms + wrap modes, submesh vertex buffers via GPU commands).
- `bflim.py` — BFLIM sprite decode (menu icons, handles rotated storage).
- `glb.py` — self-contained GLB writer: embedded PNG textures, per-material
  UV-frame transforms baked into TEXCOORD_0, mirror/repeat samplers, alpha
  blending for overlay maps (iris/pupil), and one-file normal/shiny texture
  variants for Pokémon rows.
- `rom.py` — game-profile dispatch. Ultra Moon keeps its exact GARC scan;
  other games use the generic named-RomFS catalog.
- `named_romfs.py` — game-neutral, header-verified catalog for named CGFX
  models, textures, and animation resources. Nothing large is held in memory;
  payloads are re-read on demand.
- `cgfx_tool.py` / `cgfx_preview.py` — isolated NintendoWare conversion path.
  It lazily builds a pinned headless SPICA bridge under `exports/.tooling` and
  converts a model with nearby textures and an idle animation to GLB through
  Assimp. The Game Freak model path does not import or invoke this bridge.
- `service.py` — preview/export services: GLB (normal + shiny), texture PNGs
  (normal + shiny), raw GFMotion packs, sprite PNGs.

## Pokémon appearance variants

3DS Pokémon GLBs should keep alternate appearance state in `extras.rae` instead
of requiring sibling GLBs. Current single-form exports embed normal materials plus
parallel shiny material siblings:

- root `extras.rae.textureVariants` remains the compatibility surface for
  consumers that only know normal/shiny
- root `extras.rae.appearanceVariants` exposes the same data as a generic
  `texture` axis so downstream tools can add `form`, `pattern`, or other axes
  without hard-coding shiny behavior
- each normal material that has a shiny sibling stores
  `extras.rae.shinyMaterialIndex`

Future species-bundle exports should add a `form` axis under
`appearanceVariants`. Texture-only forms should use per-material
`extras.rae.formMaterialIndices`; geometry forms should use node
`extras.rae.visibleForForms`. Full-geometry forms get independent skeleton
nodes, independent skins, and animation channels targeting that form's bones, so
they do not borrow the default form's rest pose. Consumers should resolve
form/material visibility first, then apply the active texture variant such as
normal or shiny.

## Pokémon Ultra Moon GARC map

| GARC | Contents |
|------|----------|
| `/a/0/9/4` | Pokémon models: slot 0 = species→group table, then 9 slots per form group (model, normal tex, shiny tex, extra tex, 4× motion, misc) |
| `/a/0/6/2` | Pokémon menu icon sprites (BFLIM 64×32 RGBA5551, own ordering) |

## USUM battle-background composition

`/a/0/8/1` stores battle environments as centered layers rather than one model
per complete scene. Compact `btl_G_*` arena centres are commonly surrounded by
wider `btl_N_*` models; closed and special stages may be self-contained. RAE's
3DS scanner attaches the matching compositions to each affected asset and the
export menu offers both complete-map GLBs/texture sets and the selected layer
alone.

Complete export combines geometry at the shared origin, resolves textures for
all layers, and carries every layer's GFMotion material tracks. The 3DS preview
plays the chosen primary ambient clip plus non-conflicting ambient overlays, so
independent coast and ocean wave motion remain active together. The mapping is
  owned by `world_composition.py`; it is not shared with the Nintendo DS module.

World GLBs preserve the PICA fragment shader's six TEV stages and up to three
independently animated texture units on one surface. Secondary vertex UV sets,
shoreline foam, ocean swell, and counter-motion therefore retain their original
30 fps GFMotion tracks without additive duplicate geometry. Long sea-gradient
motions also provide the daylight bind pose used by the default preview.
World environments export explicit base-water and overlay roles. Independent
short ambient loops remain active as overlays, while palette-selection tracks
are held at the chosen time/weather state instead of advancing as ambient UV.
World-motion export converts GF spatial texture-matrix translation to the
opposite GL map-offset direction once at this boundary; palette/time atlas
selectors retain their authored coordinates. Consumers use the exported
offsets directly and must not apply another direction reversal.
Single-texture materials retain the same UV-motion contract even when they do
not require a full TEV payload. Visibility tracks are keyed to exported nodes;
when the shipped GFModel merges logical effect shapes into an optimized mesh,
the export records the contributing source names in `meshVisibilitySources`.

Community mappings live in `mappings/3ds/`.

## Generic named-RomFS games

Unknown decrypted 3DS games are no longer sent through Ultra Moon's anonymous
GARC assumptions. RAE catalogs direct CGFX resources by both extension and
`CGFX` file signature:

| Extensions | Catalog rows |
|------------|--------------|
| `.bcmdl`, `.bcres`, `.cgfx` | Models/resources (`CGMD`) |
| `.bctex` | Textures (`CGTX`) |
| `.bcskla` | Skeletal animations (`CGSA`) |
| `.bcmata` | Material animations (`CGMA`) |
| `.bccam` | Camera animations (`CGCA`) |

This fallback immediately benefits games that keep NintendoWare assets as
named RomFS files. Games that hide them inside proprietary archives still need
a small game profile/archive reader, but do not require changes to the verified
Ultra Moon scanner.
