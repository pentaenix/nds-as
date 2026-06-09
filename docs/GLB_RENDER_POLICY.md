# GLB render policy (RAE)

RAE post-processes apicula GLB exports so every consumer can render DS models
without game-specific name hacks.

## Pipeline

1. **apicula** writes geometry, textures, and `extras.rae.nitro` per material.
2. **`rae.glb_policy.apply_glb_policy`** rewrites each material with canonical
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

## Manual override (editor only)

Future RAE UI may allow overriding `extras.rae.renderClass` for odd titles. Overrides are never inferred from names.
