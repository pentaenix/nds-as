# Pokemon Resort `.tile` bundle

The Nintendo DS export module can package a selected `BMD0` model as a portable
Pokemon Resort tile. Choose **Tile: Pokemon Resort (.tile)** from **Export
Selected**. This profile is owned by `src/platforms/nds/export_module/` and does
not change RAE preview or rendering behavior.

## Container

Extension: `.tile`

Container: ZIP with forward-slash entry paths

Manifest format: `pokemon_resort.tile`, version `1`

```text
water.tile
├── manifest.json
├── model.glb
└── textures/
    └── water_mat/
        ├── frame_000.png
        └── frame_001.png
```

`model.glb` is self-contained and uses the same NDS-owned conversion and
material policy as RAE's normal GLB export. Static props such as trees simply
have an empty `materials.animations` list.

## Manifest

```json
{
  "format": "pokemon_resort.tile",
  "version": 1,
  "name": "sea_water",
  "source": {
    "tool": "RAE",
    "platform": "nds",
    "assetId": "asset-id",
    "virtualPath": "a/1/2/3/water.nsbmd",
    "magic": "BMD0"
  },
  "model": {
    "path": "model.glb",
    "format": "glb"
  },
  "materials": {
    "animations": [
      {
        "material": "water_mat",
        "type": "frames",
        "state": "play",
        "frames": [
          "textures/water_mat/frame_000.png",
          "textures/water_mat/frame_001.png"
        ],
        "frameDurationMs": 150,
        "loop": true,
        "phase": "global"
      }
    ]
  },
  "defaults": {
    "tags": [],
    "properties": {},
    "renderMode": "cutout",
    "collision": { "mode": "none", "autoApply": false }
  }
}
```

Each animation names the GLB material that receives the frame sequence. Other
materials remain static. RAE uses the active texture-animation state when one
is authored, otherwise it exports the detected numbered frame family in numeric
order. The Map Editor may override the suggested name, footprint, tags,
rendering mode, frame timing, and collision behavior during import.

The current RTPKS runtime supports material frame animation. Skeletal and node
animation clips embedded in the GLB are not converted into tile motion; the Map
Editor imports their geometry at bind pose.
