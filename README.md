# NDS-AS — Nintendo DS Asset Studio

[nds-as](https://github.com/pentaenix/nds-as) is a local desktop app and CLI for exploring **your own legally dumped Nintendo DS `.nds` ROMs**. It is built for Pokémon DS research first, while keeping a generic DS asset-browser path.

NDS-AS does **not** download ROMs, bypass copy protection, or include extracted copyrighted assets.

## What v0.25 focuses on

- Fast ROM indexing without heavy upfront analysis at load time.
- Lazy mapped folders that stay open while you preview assets or load textures.
- A right-side preview inspector: visual preview on top, selected-asset details and action buttons below.
- Single-click preview for readable assets.
- Selected-only smart export.
- Session saves in `saves/`, ignored by git.
- Deterministic model texture resolution. NDS-AS parses NSBTX texture dictionaries and only treats exact decoded texture/palette bindings as real; fuzzy path/name candidates stay out of the normal preview path.
- Regular model preview writes embedded/exact decoded texture PNGs for NDS-AS fallback preview, not only Resolve Texture.
- Embedded NSBMD textures are reported clearly as embedded data instead of pretending there is an external BTX0 pin.
- Raw Folders browser tab for drilling directly into ROM folders like `a/2/3/3`, `a/0/0/8`, or `a/0/1/4` when mapped labels are not enough.
- Texture archive contact sheets can decode strict resolved pairs or all palette variants for inspection.
- Model preview supplies only resolved/manual texture assets to apicula instead of throwing a large candidate pile at conversion.
- Resolver reports explain exactly why a texture is verified, manually overridden, or unresolved.
- DS 4x4 compressed BTX0 texture decoding for many Pokémon texture archives.
- Corrected Nitro 2D offset handling for NCLR/NCGR/NSCR previews.
- Smarter same-folder sprite pairing for NCER/NANR/NCGR/NCLR groups.
- Vendored [apicula](https://github.com/scurest/apicula) source under `tools/apicula/` (build locally with Cargo).

## Safe local folders

These folders are local work areas and are ignored by git:

```text
roms/                put your own legally dumped ROMs here
exports/             extracted/converted outputs
saves/               NDS-AS session files
mapping_overrides/   local mapping notes/discoveries
```

The repo keeps only `.gitkeep`/README files inside those folders.

## Install or refresh

Run this after cloning or downloading the repo, and again after any NDS-AS update:

```bash
./dsas install
```

Then launch the app:

```bash
./dsas run
```

On Windows, use:

```bat
dsas.bat install
dsas.bat run
```

## Common workflow

1. Put your own `.nds` dump in `roms/`.
2. Run `./dsas run`.
3. Open the ROM.
4. Browse the mapped tree or use filters like `BMD0`, `BTX0`, `cat:move-effects`, `mapped`, or `unmapped`.
5. Select an asset to preview it.
6. For models, use **Set Textures** to parse exact material/texture bindings and refresh the preview.
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

Pokémon DS games often store map models and texture packs separately. NDS-AS therefore:

- matches models to texture archives through mapping/context and Nitro material/texture names;
- lets you pin a BTX0 manually as an override;
- uses **Resolve Texture** to parse exact material names and NSBTX texture/palette dictionaries;
- records the report in the preview inspector/details;
- keeps the final apicula GLB/DAE files intact while baking textures only for NDS-AS's own preview widget.

If NDS-AS cannot prove a decoded image is bound to the model, it reports **Texture unresolved** instead of pinning a weak candidate. Manual BTX0 pinning is still available as an override, but it is labeled honestly as user-selected.

## Audio

NDS-AS detects and exports common Nintendo DS sound formats:

```text
SDAT  sound archive
SSEQ  sequenced music/SFX
SSAR  sequence archive
SBNK  instrument bank
SWAR  wave/sample archive
SWAV  sample
STRM  streamed audio
```

For best quality, NDS-AS always exports original raw audio pieces first. It also writes WAV previews when the data is sample/stream PCM that NDS-AS can decode. Optional MP3 output requires `ffmpeg` and is treated as a convenience copy, not the quality master.

## CLI examples

```bash
./dsas info roms/game.nds
./dsas list roms/game.nds --query BMD0
./dsas decode roms/game.nds --out exports/readable --query BTX0
./dsas convert roms/game.nds --out exports/models --query BMD0 --format glb
./dsas audio roms/game.nds --out exports/audio
./dsas mappings
```

## Optional apicula

`apicula` is a Rust CLI used for model conversion/preview. The vendored source lives in `tools/apicula/`; `./dsas install` builds it when Rust/Cargo is available. Listing, raw export, PNG texture/tile decoding, and audio extraction still work without it, but model preview/conversion needs it.

NDS-AS looks for apicula on `PATH`, in `DSAS_APICULA` (or legacy `DSM_APICULA`), and at:

```text
tools/apicula/target/release/apicula
```

Manual build:

```bash
cd tools/apicula
cargo build --release
```

## Development sanity check

```bash
PYTHONPATH=src python -m pytest -q
```
