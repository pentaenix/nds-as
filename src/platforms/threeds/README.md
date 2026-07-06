# 3DS platform (active)

Nintendo 3DS support for **decrypted** dumps (`.3ds`, `.cci`, `.cxi`). RAE never
ships or applies AES keys — encrypted images are detected and reported.

Verified against a legal *Pokémon Ultra Moon* cartridge dump (NoCrypto `.cci`).

## Pipeline

- `container.py` — NCSD/NCCH headers, RomFS (IVFC level 3) tree, GARC v4/v6 tables.
- `lz11.py` — LZ11 decompression (duplicated per island rules; no NDS import).
- `pica.py` — PICA200 command-stream reader + texture decode (swizzled 8×8
  Morton tiles; RGBA8/RGB8/RGBA5551/RGB565/RGBA4/LA8/L8/A8/LA4/L4/A4/HILO8/ETC1/ETC1A4).
- `gf.py` — Game Freak GFModel/GFTexture parsing (bones, materials with texture
  units + UV transforms + wrap modes, submesh vertex buffers via GPU commands).
- `bflim.py` — BFLIM sprite decode (menu icons, handles rotated storage).
- `glb.py` — self-contained GLB writer: embedded PNG textures, per-material
  UV-frame transforms baked into TEXCOORD_0, mirror/repeat samplers, alpha
  blending for overlay maps (iris/pupil), and one-file normal/shiny texture
  variants for Pokémon rows.
- `rom.py` — scan: emits lightweight JSON descriptor rows (species/forms from
  the model GARC header table, sprites from the icon GARC). Nothing large is
  held in memory; payloads are re-read on demand.
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

Community mappings live in `mappings/3ds/`.
