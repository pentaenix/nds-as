# Pokémon HOME mobile asset support

This patch adds an initial Android/Unity/HOME source backend to RAE.

## What it does now

- Scans folders, APK/APKM/XAPK/OBB/ZIP containers, copied Android `files/` and `cache/` folders.
- Detects HOME-style paths such as `Models/Android/.../pokemons/pm0906_00_00`.
- Detects `.aba` / `.abap` HOME package candidates but does **not** decrypt or bypass them.
- Detects readable Unity bundles by magic (`UnityFS`, `UnityWeb`, `UnityRaw`).
- Optionally inventories Unity object types when `UnityPy` is installed.
- Groups files into Pokémon form packages using IDs like `pm0054_00_00`.
- Maps National Dex numbers to display names for seeded names, including `pkm_54 -> Psyduck`.
- Classifies animation clip names into `idle`, `physical_attack`, and `special_attack` candidates when clips are readable.
- Adds CLI commands under `rae home ...`.
- Adds a File-menu action to open a Pokémon HOME source folder in the UI and browse grouped package rows.

## What it intentionally does not do

- No embedded decryption keys.
- No server scraping.
- No runtime memory hooks.
- No automatic CloudFront downloads.
- No protected asset bypass.

If a source contains encrypted `.aba/.abap`, RAE reports it as unsupported. Use local readable cache files or user-configured external tools outside this patch.

## Recommended local source

Start with a copied Android folder from your own device/emulator:

```text
Android/data/jp.pokemon.pokemonhome/files/
Android/data/jp.pokemon.pokemonhome/cache/
Android/obb/jp.pokemon.pokemonhome/
```

## Commands

```bash
./rae home scan /path/to/home/source --out exports/home_inventory.json
./rae home list /path/to/home/source
./rae home report exports/home_inventory.json
```

To enable object-level Unity inventory:

```bash
.venv/bin/python -m pip install UnityPy
```

Then re-run the scan. RAE will try to list Mesh, SkinnedMeshRenderer, Texture2D, Material, Animator, Avatar, and AnimationClip objects from readable Unity bundles.

## Next milestone

This patch creates the HOME library index and the object/dependency layer. The next patch should add external export adapters:

- AssetStudio CLI / GUI handoff for Animator + AnimationClip FBX export.
- AssetRipper project reconstruction for tricky Unity versions.
- Blender import/export bridge for normalized GLB output.
- RAE viewport playback for exported GLB/FBX animation clips.
