# Pokemon Resort `.tile` bundle

The Nintendo DS export module can package a selected `BMD0` model as a portable
Pokemon Resort tile. Choose **Tile: Pokemon Resort (.tile)** from **Export
Selected**. This profile is owned by `src/platforms/nds/export_module/` and does
not change RAE preview or rendering behavior.

For a map model that contains many surfaces, preview the model and open the
DS-only **Tile Extractor** inspector tab. Check one or more material parts, such
as a pond, shore, cliff, or tree, then choose **Export selected as .tile…**.
The extractor keeps only geometry that uses those materials and preserves the
textures currently visible in the preview. Correct any questionable binding in
**Texture Assigner** before extraction.

Tile Extractor is a side-by-side browser: the material/tile list is on the left
and a dedicated interactive viewport is on the right. The viewport is created
only when the tab is opened, so it does not add another WebEngine viewer to RAE
startup. Clicking a row previews the exact isolated geometry immediately,
including flat geometry, and starts that material's default DS animation when
one exists. A dependency-free snapshot remains available as a fallback when
the live viewer is unavailable. Checkboxes decide which parts are combined in
the exported tile, and double-clicking a row toggles its export check.

One material can occur many times in a map. The **Tile _n_ / _count_** controls
select one disconnected occurrence, so choosing a tree, flower, shore segment,
or prop does not export every occurrence that shares its material. A connected
line or plane spanning several map cells is split using its geometry-to-UV
repeat scale; for example, tree footprints resolve to 32-by-32 while `kusa`
grass resolves to 16-by-16. The arrows choose the one occurrence that preview
and export will use.

When a focused row has more than one inferred occurrence, **Export all
_count_…** writes every arrow-selectable occurrence to a chosen folder as an
independent `.tile` bundle. Names include the shape, footprint, and serial so
straight `1x3`/`3x1` pieces remain distinct from `3x3` corners. Each output is
cropped and recentered separately and keeps its complete semantic layer stack.
Animation frames are baked once per material stack during the batch and copied
into each portable archive, so batch export does not repeat the expensive
motion decode for every occurrence.

Every exported bundle also contains `preview.png`, a transparent top-down
orthographic snapshot of the exact isolated geometry. Pokemon Resort Admin
stores that snapshot with the RTPKS metadata and uses it in both the tile
selector and map placement views. Older bundles without a snapshot remain
compatible and are rendered from their mesh when the editor first needs them.

For other large flat surfaces with repeated UVs, **Repeat unit** reduces the
selected plane to one texture-repeat-sized quad.

Some Gen 5 map objects are drawn as several batched material planes rather
than one connected mesh. Tree families such as `ki02ax`, `ki02bx`, `ki02c`,
and `ki02dx` now appear as one **Tree ki02** row. `kusa_ec1`, `kusa_ec2`, and
`kusa_ec3` likewise appear as one **Grass kusa_ec** row. The extractor anchors
each family from its lowest horizontal footprint, then clips every stacked or
crossed layer to the same occurrence. This avoids retaining two trees from one
batched billboard or four grasses from a 32-by-32 crop. Trees keep the shadow
material authored as part of their `ki*` family, but the extractor deliberately
excludes unrelated grass, path, sand, and rock floors. Loose rocks likewise do
not inherit the terrain underneath them, so the same tile can be placed on
grass, dirt, or a mountainside.

**Assemble layers** is feature-aware rather than proximity-only. Water rows may
add water, reflection, foam, and shore materials at their original heights;
cliff rows may add other cliff layers. A generic material part or a shadow such
as `h_kage` cannot collect an overlapping building, bridge, tree, or pond. Cave
and similar multi-island props are clustered into complete object occurrences,
so their trim/opening faces do not appear as mysterious standalone "window"
tiles.

`mizutama01` puddles appear as a dedicated **Puddle** row. Their authored edge,
corner, and center footprints are preserved and the deeper `mizu_sita2`
reflection/sky plane is included automatically even though it is 32 source
units below the surface.

Black 2/White 2 beach seams such as `sea_simi_1`, `sea_zanami`, and
`sea_zanami2` appear as one **Beach shore** row. RAE follows their native
16-unit grid: a straight coastline choice exports a 16-by-48 footprint (1x3,
from land into water), while inward and outward corners export 48-by-48 (3x3).
The animated foam and wet-sand layers stay together, but the map-wide
`sea_mizu*` planes are exported as a separate water-body tile. This gives the
pack builder one canonical source for the two ocean planes. Resort keeps the
shoreline tile and the water-body tile independent: paint the water body on a
lower decoration layer and the coastline overlay above it. The pack never
duplicates the body planes inside a shoreline footprint.
For the shoreline itself, RAE preserves the independent ROM-authored
`sea_zanami` crest and `sea_zanami2` underlay channels; they do not share one
synthetic UV track. This retains their slow opposing lateral phases. Only the
crest's shore-normal excursion is reduced around its authored maximum advance,
so it still reaches the top while its retreat does not expose the complete wave strip. Open-ocean
`sea_mizu*` UV scrolling is preserved.
Waterfall families are grouped as one tall feature and include their nearby
crest, foam, and bottom-wave layers; spatial filtering keeps the rest of the
river out of that tile.

Spatial extraction clips the source triangles and interpolates their UVs. It
does not replace an irregular shoreline with a rectangular quad, so an animated
water/rock seam cannot fill the land-side corner merely because its component
bounding box crossed the selected tile. Flat water layers that occupy the same
footprint are assembled together automatically.

Choose **Open in main** only when a larger interactive inspection is useful.
It uses the same isolated, texture-embedded GLB builder
as export, so unrelated instances and hidden map geometry are not included.
**Source** returns to the source map without changing what Export will package.

## Generation V placed map objects

Black 2/White 2 map entries are composite containers. In
addition to the terrain `BMD0`, a container can carry a list of separately
placed 3D objects (commonly called building positions). For a carved
`a/0/0/8` map, RAE opens the DS-only **Map Objects** inspector and resolves the
exact map automatically in a background worker as soon as the map preview
opens. The retry button appears only if exact resolution fails. RAE follows the
map's MapMatrix, ZoneData, and AreaData
records to select the authoritative terrain texture, BTA0 material motion, and
external inside/outside building model and texture pack. It then decodes the
full 16-bit model ID and free-angle rotation in every placement, converts the
DS Z axis for glTF, and composes those models with the exact terrain. Headerless
matrices use their ZoneData matrix reference, which is required by maps such as
file 397.

Exact resolution applies even when the placement list is empty: authoritative
terrain textures and material animation do not depend on a house being present.
Transient preview/ROM handoff failures are retried automatically, so the first
map open is sufficient in normal use. If the slower generic texture resolver
finishes after exact-map composition, the NDS inspector detects that late
handoff and restores the authoritative exact GLB. The exact animation Play
action performs the same check before starting a clip, which prevents the
animation list from controlling a stale generic model.

The inspector keeps variant, view, retry, and a single **Export…** action in one
compact row. Its table receives the remaining vertical space, and the visible
splitter above the inspector can be dragged to trade space with the 3D viewport.
The view menu switches between **Map + models** and **Terrain only**. Either
view becomes the Tile Extractor source, so a pond or ocean
surface is extracted from the corrected map rather than an earlier heuristic
texture preview. Extracted geometry is centered over the origin. Ordinary props
use their lowest visible point as the ground plane. Layered water and shore
features instead use the semantic land/seam datum, recorded as
`extras.rae.tileBounds.originY` in the GLB. Pokemon Resort Admin preserves that
datum: sand or rock meets the map at height zero while foam, water, reflection,
and cloud layers keep their authored negative offsets.
The export modal's **Current map** action writes that active composed GLB—not the carved terrain-only
asset—so placed buildings, embedded textures, material motion, and model motion
are all present. The normal **Model: GLB** export profile also detects an active
exact-map build and writes the same composed result. This prevents a fountain
or full-map export from becoming an empty/terrain-only GLB.

The **Variant** control pairs geometry with its matching AreaData
record. Black 2/White 2 commonly reuse spring geometry for spring, summer, and
autumn while changing the texture and lighting record; winter may use a
separate `winter*` or `white*` snow mesh. **All seasons / variants** in the
export modal builds every
listed pair as a self-contained GLB. Unreferenced seasonal and showcase maps
are resolved through a coordinate-identical live map or, when no matrix entry
exists, by exact texture-name coverage plus compatible placed-model IDs. An
auxiliary `map_out*` model whose textures are absent from the ROM is reported
as such instead of being displayed as a misleading white map.

Selecting a row exposes the exact model as a standalone preview; double-clicking
is the quick preview action. The export modal then adds only the applicable
doorless GLB, animated-door GLB, and Pokemon Resort `.tile` actions. Door pieces,
signs, trees, and complete buildings are treated uniformly; the list reports
the original position, rotation, pack, and model index instead of guessing from
terrain material names. This resolver is owned entirely by the NDS platform and
does not change the shared renderer or another platform's modules.

Black/White and Black 2/White 2 place their AB building packs in different
NARC paths. RAE selects the release-specific model/texture pair from its actual
`AB` and `BTX0` members, then validates the pack against the map's placed model
ids. An AB building definition may reference a separate door model at bytes
`0x04..0x05` followed by its signed local X/Y/Z offset. Exact-map composition
adds the door's closed state at that offset. Standalone building preview, GLB,
and `.tile` export include the same door plus its embedded open/close clips.
Door skeletal and material clips are interaction states: exact maps keep their
bind-pose door closed, and a focused building waits for the Animations tab's
Play action instead of looping the door automatically. Shared door UIDs omitted
from an individual AB pack are resolved from the release's other AB bundles.
The signed XYZ values following the AB door id are building-local coordinates;
their Z value is not subjected to the separate world-map Z conversion. This
keeps entrances such as `pc_01` on the front façade instead of mirroring them
through the building. Map Objects previews use the correctly assembled model
and offer two explicit GLB exports: **doorless** for runtimes that place a
separate door tile, and **with animated door** with its named open/close clips.
NDS export dialogs start under `exports/` (`buildings/`, `doors/`, `maps/`, or
`tiles/`) rather than the repository root.

**All discovered doors** in the export modal writes every explicit or high-confidence event-warp door
from the resolved map as its own `.tile`, rather than exporting the complete
building assembly. Each manifest carries `interaction.door`,
`interaction.kind: door`, the local front, open/close clip semantics, model and
placement indices, source offset and rotation, discovery method, confidence,
and any known destination zone/interior family. Pokemon Resort Admin can import
the batch into an RTPKS pack without re-entering that provenance. During import,
named node/skeletal clips are sampled into compact RTPKS vertex timelines while
material-motion doors retain their exact UV tracks. The runtime addresses those
clips per placed door, so copies of one tile animate independently. The first
frame of the configured open clip becomes the resting geometry; this also keeps
vertical doors such as `pcev02` below the floor until their upward clip runs.

AreaData material animation is resolved again after terrain and placed objects
are composed. This matters for models such as `wbt_fountain`: its
`wbt_foun_01*` and `wbt_foun_02*` tracks live in the map BTA resource even
though the animated materials live in the separately placed fountain model.
Standalone object preview starts its default material clip, and both standalone
and composed GLBs retain the track in `extras.rae.mapMaterialMotion` plus
apicula-compatible `EXT_property_animation` channels.

Some Gen 5 props carry their animation inside the `AB` building definition
rather than AreaData. RAE extracts those embedded NSBCA/NSBTA/NSBTP/NSBMA/
NSBVA resources and supplies them when converting the placed model. For
example, map 11's `c01fountain_01` record embeds a BTA clip with independent
tracks for `c01fountain_a_2`, `c01fountain_c_2`, and `c01fountain_d_1`.
The composed map adds an **Exact map ambient animations** default clip so area
water, texture-pattern effects, and prop animations run together while each
track keeps its own loop length. Standard embedded NSBTP files are decoded as
real material/image keyframes; this restores animated river rocks and similar
props, and the same frames are baked into `.tile` exports. Standalone props keep
their separate skeletal states (for example day/evening/night) in the animation
list. A composed map uses only each prop's default state before combining the
independent props into **Exact map object animations**, avoiding the old failure
where mutually exclusive states ran together and hid backdrops or distorted
doors. The NDS inspector starts model motion alongside the selected material
clip, so a windmill or model fountain does not disable water and shoreline
motion. The `sea_gake02` rock/water seam is bounded
to its local wave phase so its 16×16 crest does not look like a whole scrolling
tile. Beach shoreline motion keeps its original lateral channels and uses the
bounded vertical retreat described below. Long `c07_foun*` fountain and
`kawa01*` waterfall streams use the 30 Hz map cadence, while the already-correct
rock/water seam retains its explicit 20 fps rate.

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
material policy as RAE's normal GLB export. Props without a Nitro material or
texture-frame animation simply have an empty `materials.animations` list.
Extracted map tiles also retain an
`extras.rae.tileSelection` record in the GLB describing the chosen disconnected
component and whether it was reduced to a repeat unit.

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
    "uvTextures": [
      {
        "material": "water_mat",
        "coordinateSpace": "world",
        "sampler": {
          "wrapS": "repeat",
          "wrapT": "repeat",
          "magFilter": "nearest",
          "minFilter": "nearest"
        }
      }
    ],
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

For Generation V maps, RAE reads the AreaData-selected BTA0 and exposes its real
UV-translation clip in the animation controls. This avoids treating unrelated
numbered names such as `gake1_1` as flipbook frames. A good-looking water texture
is not necessarily animated: if the selected surface has no BTA0 track or frame
family, it is correctly treated as static. Animated tiles show **Play
animation** in the Map Editor Tile Pack Editor after import.

RAE also reads the separate Gen 5 area-pattern stream used for foam, waterfall,
flower, river-edge, and shimmer texture swaps. Exact DS clips appear in the
DS-only **Animations** inspector as a selectable list with Play and Stop, and
the authoritative default clip starts automatically. When an animated surface
is extracted, RAE stores the signed BTA UV-offset timeline as `materialMotion`
and writes only actual texture-pattern changes as sparse PNG keyframes. It does
not rasterize ordinary water scrolling into a pixel-animation flipbook. The
exported model is the selected repeat-sized plane or object, not the original
full-map water plane; the timeline still targets its original material name, so
Resort can tile the geometry while recreating the same scrolling water, clouds,
foam, or river-edge motion.

The embedded GLB preserves the Nitro material's clamp, repeat, or
mirrored-repeat sampler and its signed UV direction; the Resort importer carries
that sampler into RTPKS. `materials.uvTextures` makes UV-driven surfaces
explicit. `coordinateSpace: "world"` tells Resort to derive and retain the
material's affine U/V change per map tile, so adjacent instances sample one
continuous texture field instead of restarting at each 1x1 anchor. RAE assigns
that mode only when all UV samples pass an exact affine-fit check. Static
textures and animated shoreline materials with multiple UV islands use
`coordinateSpace: "mesh"` and keep their authored local UVs.
Overworld map motion preserves every BTA sample against Generation V's 30 Hz
source clock. Ambient material tracks advance one stored sample every two map
ticks, so their exports carry `timebaseHz: 15`, `sourceTimebaseHz: 30`, and
`interpolation: "step"`; `frameDurationMs` remains only as a compatibility
fallback. RAE, Admin, and the C++ runtime derive the active sample from elapsed
time, keeping playback unchanged at 30, 60, or uncapped display FPS. Shoreline
exports also preserve the named
layer role and display order (`sea_zanami2`, `sea_simi_1`, `sea_zanami`) so a
standalone tile is composited like the source map. The beach
crest keeps its original lateral channel and loop timing while its vertical
excursion is limited around the authored maximum shoreline advance. That upper
endpoint remains unchanged, while the decoded 16.65625-pixel full-strip retreat
becomes 8.328125 pixels. The already-correct
`sea_gake02` rock/water seam keeps its per-track 20 fps (50 ms) rate. Skeletal
object timelines are doubled in duration as well. This keeps water and props
calm enough for Resort without throwing away the original frame samples.
Lake surfaces, shoreline strips, beach foam, shallow water, cliff water, and
under-water/reflection planes remain separate material tracks. Combining them
in one logical tile therefore does not turn the entire body of water into one
uniform scroll: each layer keeps its own direction, range, sampler, UV timeline,
and sparse pattern keyframes.
Gen 5 waterfall bodies use wide signed 20.12 BTA translation samples, while
most overworld water uses compact signed 10.5 samples. RAE decodes both forms.
In the Tile Extractor, focusing a `kawa01*`, `taki_sakai`, or `taki_shibu`
material and enabling **Assemble layers** selects the falling body, under-water
layer, lip, and splash together. The resulting `.tile` is one placeable,
multi-material waterfall feature and keeps every layer's independent motion.
RAE also carries the isolated GLB's alpha mode into the import suggestion:
blended water opens as **Transparent blend**, cutout foliage opens as
**Cutout**, and fully opaque tiles open as **Opaque**. Nitro uniform material
alpha is preserved in live preview and in the GLB material. The Resort importer
converts that value to its 0-31 runtime material alpha instead of assigning the
same hard-coded translucency to every blended tile.

Map/model composition uses a direct GLB merger owned by the NDS platform; it
does not import `trimesh` or `numpy` during startup or composition.

The RTPKS runtime supports material frame animation, RAE's exact material-motion
timeline (UV offsets plus sparse image keyframes), and named node/skeletal clips
baked to vertex timelines by Pokemon Resort Admin. Triggerable tile instances
select the configured open or close clip without sharing playback state with
another placement of the same tile.

NDS building and tile exports are placement-ready: RAE adds a scene parent that
centers the complete mesh on X/Z. Buildings and ordinary props place their
lowest visible point on Y=0; layered shore/water tiles place their land or seam
surface on Y=0 and retain the lower water planes.
The correction preserves the original mesh buffers, skins, animation targets,
materials, and material-motion metadata. Resort can therefore place either the
exported building GLB or its `.tile` bundle at map height zero without a hidden
source-model offset making it float, sink, or sit off-center.

## Canonical Generation V ocean set

The Resort Water tab uses a small, explicit grammar instead of exporting every
map occurrence as an unrelated tile:

- beach: four outer 3x3 corners, four inner 3x3 corners, and four directional
  1x3/3x1 strips;
- open ocean: one repeatable, two-layer 1x1 body;
- rock/water: four inner 1x1 corners, four directional 1x1 strips, and four
  rounded 2x2 outer corners (the DS geometry naturally spans two cells);
- one 1x1 shallow-water surface and one authored dive-spot field.

The coastline and rock shapes reuse exact DS-authored meshes and UVs from the
initial RTPKS, while their material-motion timelines come from RAE's NDS
decoder. The Water-tab records are independent runtime aliases with stable tile
ids, so the result does not require PDSMS to remain installed. Rebuilding the
tab is safe to repeat: shared source materials remain alive, stale ocean-profile
entries are retired, and embedded top-down previews are replaced atomically.
The open-ocean body's opaque lower plane and translucent upper plane carry
source-derived world UV bases, which keeps their independent scroll directions
continuous across adjacent 1x1 placements. Beach and rock transitions retain
their authored mesh UV islands and are never assigned the body's world basis.

## Canonical Generation V grass set

The Resort Grass tab can be rebuilt from exact Black/White map cells without
Pokemon DS Map Studio. `grass01ax` supplies a repeatable 1x1 ground cell.
`ue_grass01` retains the AreaData `BTA0` UV-motion track (240 source frames,
30 Hz Nitro source cadence, 15 Hz overworld playback). `yamagrs01` is reduced
from repeated map occurrences to eight walkable 1x1 height fields: four
directional straight ramps and four high-corner ramps. Each bundle embeds its
top-down orthographic preview and preserves a zero-height lower edge with a
one-tile upper edge for Resort ramp pairing.

## Headless NDS tile export API

The tile extractor is also available as a Qt-independent JSON request API for
automation and editor integrations. Discovery returns stable candidate IDs plus the
exact source bounds, layered material stack, footprint, source occurrence, and
grounding height. Export accepts those IDs and writes the same portable `.tile`
bundles as the modal, including embedded GLB geometry, material motion, sparse
pattern frames, and the top-down preview.

From an installed or editable RAE checkout, send a request on stdin:

```sh
python -m rae.platforms.nds.export_module.api <<'JSON'
{
  "apiVersion": 1,
  "action": "discover",
  "sourceGlb": "/path/to/exact_map/terrain/map24_08.glb",
  "materials": ["sea_simi_1"]
}
JSON
```

Use the returned IDs in a second request with `"action": "export"`, an
`"outputDir"`, and `"candidateIds"`. Omitting `candidateIds` (or using
`["*"]`) exports every discovered occurrence. The same contract is callable
in-process through `handle_tile_export_request`, so a future local HTTP/MCP
adapter does not need to reproduce map-inspector state or tile-bundle logic.
