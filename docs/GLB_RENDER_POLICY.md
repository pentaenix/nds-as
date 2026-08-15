# Nintendo DS GLB rendering policy

> **Isolation:** Material classification for DS belongs in `platforms/nds/gltf/`.
> Each console duplicates its own `gltf/` tree — there is no shared top-level package.
> See [`docs/development/platform-isolation.md`](development/platform-isolation.md).

RAE post-processes apicula GLB exports so every consumer can render DS models
without game-specific name hacks.

## Pipeline

1. **apicula** writes geometry, textures, and `extras.rae.nitro` per material.
2. **`platforms/nds/gltf/apply.apply_glb_policy`** rewrites each material with canonical
   `alphaMode` / `doubleSided` and `extras.rae.renderClass`.
3. **Consumers** (RAE WebEngine preview, pokemon-resort-page, pokemon-resort C++)
   honor `extras.rae.renderClass`.

## Schema (`extras.rae`)

```json
{
  "schemaVersion": 1,
  "renderClass": "uniform_decal",
  "nitro": {
    "alpha": 0.29,
    "cullBackface": true,
    "cullFrontface": false,
    "textureAlpha": "opaque"
  },
  "signals": {
    "textureMeaningfulAlpha": false,
    "horizontalFaceFraction": 0.97
  }
}
```

## `renderClass` values

| Value | When | glTF fields |
|-------|------|-------------|
| `opaque` | Solid surfaces; PNG alpha is a DS rip artifact | `OPAQUE`, `doubleSided` from Nitro cull |
| `mask` | Texture has meaningful cutout alpha (≥0.5% texels below cutoff) | `MASK`, `alphaCutoff: 0.5` |
| `blend` | Nitro translucent texture format or non-horizontal fractional alpha | `BLEND` |
| `uniform_decal` | Fractional **uniform** material alpha, opaque texture, ≥80% horizontal faces | `BLEND`, `doubleSided: true` |

## Classification rules (no name heuristics)

`uniform_decal` requires all of:

- Material alpha strictly between 0 and 1.
- Linked PNG has **no** meaningful transparency (`pngHasMeaningfulTransparency`, cutoff 0.5, min fraction 0.5%).
- ≥80% of material triangles have face normals with `|n · Y| ≥ 0.85` in apicula model space (+Y up).

If alpha is fractional and the texture is opaque but geometry is not horizontal, class stays `blend`.

## Consumer obligations

- **Preview (three.js):** `uniform_decal` → transparent, `depthWrite: true`, `DoubleSide`, `renderOrder: 0`.
- **pokemon-resort SDL:** treat `uniform_decal` as cutout pass with `baseColorFactor` alpha; sort decals before coplanar walls.
- **Do not** infer shadows from material or mesh names.

## Optional `materialRole` (3DS Pokémon eyes)

Some GF models tag eye layers in `extras.rae.materialRole`:

| Value | Material names | Preview behavior |
|-------|----------------|------------------|
| `eye_sclera` | `Eye` | `renderOrder: 2`, normal depth write |
| `eye_iris` | `LIris`, `RIris`, `*Iris` | `renderOrder: 3`, depth write on cutout/mask; small polygon offset |

Bind-pose eye expression metadata is on `extras.rae.eyeSheet` for sclera (`Eye`) only: `cols`, `rows`, `scale`, `translation`, `wrap`, and `uvLayout: "raw_u"` (model U is **not** baked; GF scale stays on `texture.repeat`). Runtime frame selection uses `extras.rae.eyeExpression`: `frameCount`, `defaultFrame` (0-based), `frameTranslations` (GF `TX`/`TY` per frame), and `frameOffsets` (`[col * scale/cols, ty - bindTy]` for three.js `map.offset` with `repeat.x = eyeSheet.scale[0]`; both eyes sample the same column and mirror). Even frames (0,2,4,6) are the left sheet column; odd frames (1,3,5,7) are the right column — mirror wrap on U maps each to the opposite eye. Iris (`LIris`/`RIris`) uses standard baked GF UVs only. Skeletal clips do **not** carry eye UV channels.

## Manual override (editor only)

Future RAE UI may allow overriding `extras.rae.renderClass` for odd titles. Overrides are never inferred from names.
