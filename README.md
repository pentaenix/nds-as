# DSM v0.22 — DS Asset Studio

DSM is a local desktop app and CLI for exploring **your own legally dumped Nintendo DS `.nds` ROMs**. It is built for Pokémon DS research first, while keeping a generic DS asset-browser path.

DSM does **not** download ROMs, bypass copy protection, or include extracted copyrighted assets.

## What v0.22 focuses on

- Fast ROM indexing without building a global relationship graph at load time.
- Lazy mapped folders that stay open while you preview, build relationships, or load textures.
- A right-side preview inspector: visual preview on top, selected-asset details and action buttons below.
- Single-click preview for readable assets.
- Selected-only smart export.
- Session saves in `saves/`, ignored by git.
- Deterministic model texture resolution. DSM parses NSBTX texture dictionaries and only treats exact decoded texture/palette bindings as real; fuzzy path/name candidates stay out of the normal preview path.
- Regular model preview now writes embedded/exact decoded texture PNGs for DSM fallback preview, not only Resolve Texture.
- Embedded NSBMD textures are reported clearly as embedded data instead of pretending there is an external BTX0 pin.
- Added a Raw Folders browser tab for drilling directly into ROM folders like `a/2/3/3`, `a/0/0/8`, or `a/0/1/4` when mapped labels are not enough.
- Texture archive contact sheets can decode strict resolved pairs or all palette variants for inspection.
- Model preview supplies only resolved/manual texture assets to apicula instead of throwing a large candidate pile at conversion.
- Resolver reports explain exactly why a texture is verified, manually overridden, or unresolved.
- DS 4x4 compressed BTX0 texture decoding, so many Pokémon texture archives now produce readable PNGs instead of “0 decoded images.”
- Corrected Nitro 2D offset handling for NCLR/NCGR/NSCR, improving palette, tile, tilemap, NCER, and NANR previews.
- Smarter same-folder sprite pairing for NCER/NANR/NCGR/NCLR groups.

## Safe local folders

These folders are local work areas and are ignored by git:

```text
roms/                put your own legally dumped ROMs here
exports/             extracted/converted outputs
saves/               DSM session files
mapping_overrides/   local mapping notes/discoveries
```

The repo keeps only `.gitkeep`/README files inside those folders.

## Install or refresh

Run this after extracting the ZIP, and again after any DSM update:

```bash
./dsm install
```

Then launch the app:

```bash
./dsm run
```

On Windows, use:

```bat
dsm.bat install
dsm.bat run
```

## Common workflow

1. Put your own `.nds` dump in `roms/`.
2. Run `./dsm run`.
3. Open the ROM.
4. Browse the mapped tree or use filters like `BMD0`, `BTX0`, `cat:move-effects`, `mapped`, or `unmapped`.
5. Select an asset to preview it.
6. For models, use **Resolve Texture** to parse exact material/texture bindings, or **Build Relationships** to list deterministic texture/animation links.
7. Use **Export Selected…** for raw, readable, model, texture, or audio exports.
8. Use **Save Session** when you want to continue later without rescanning the ROM.

## Model textures

Nintendo DS models commonly use:

```text
BMD0 / NSBMD  model geometry, materials, sometimes textures/palettes
BTX0 / NSBTX  texture and palette packs
BCA0 / NSBCA  joint animations
BTA0/BTP0/... material and texture animation variants
```

Pokémon DS games often store map models and texture packs separately. DSM therefore:

- matches models to texture archives through mapping/context and Nitro material/texture names;
- lets you pin a BTX0 manually as an override;
- uses **Resolve Texture** to parse exact material names and NSBTX texture/palette dictionaries;
- records the report in the preview inspector/details;
- keeps the final apicula GLB/DAE files intact while baking textures only for DSM's own preview widget.

If DSM cannot prove a decoded image is bound to the model, it reports **Texture unresolved** instead of pinning a weak candidate. Manual BTX0 pinning is still available as an override, but it is labeled honestly as user-selected.

## Audio

DSM detects and exports common Nintendo DS sound formats:

```text
SDAT  sound archive
SSEQ  sequenced music/SFX
SSAR  sequence archive
SBNK  instrument bank
SWAR  wave/sample archive
SWAV  sample
STRM  streamed audio
```

For best quality, DSM always exports original raw audio pieces first. It also writes WAV previews when the data is sample/stream PCM that DSM can decode. Optional MP3 output requires `ffmpeg` and is treated as a convenience copy, not the quality master.

## CLI examples

```bash
./dsm info roms/game.nds
./dsm list roms/game.nds --query BMD0
./dsm decode roms/game.nds --out exports/readable --query BTX0
./dsm convert roms/game.nds --out exports/models --query BMD0 --format glb
./dsm audio roms/game.nds --out exports/audio
./dsm mappings
```

## Optional apicula

`apicula` is a Rust CLI used for model conversion/preview. DSM's installer attempts to detect or build it. Listing, raw export, PNG texture/tile decoding, and audio extraction still work without it, but model preview/conversion needs it.

DSM looks for apicula on `PATH`, in `DSM_APICULA`, and at:

```text
tools/apicula/target/release/apicula
```

Manual build:

```bash
git clone https://github.com/scurest/apicula.git tools/apicula
cd tools/apicula
cargo build --release
```

## Development sanity check

```bash
PYTHONPATH=src python -m pytest -q
```
