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

## Ready vs locked species (browser tree)

When a HOME mobile ROM is scanned, RAE indexes `external_files/files/Cache`
with AssetStudioModCLI and splits species rows into two tree folders:

- `1 ready to preview (in HOME Cache)` — the species' model was downloaded by
  the app (you viewed it in HOME); it previews and exports instantly.
- `2 locked (view in HOME app to unlock)` — only key-encrypted `.aba` stubs
  exist. Open the Pokémon once in the HOME app, then re-run
  Device Toolkit → Extract Installed App ROM; it moves to "ready".

The index is fingerprinted against the Cache contents, so a re-extracted ROM
automatically rebuilds it.

## App UI assets (icons, sprites, fonts, audio)

`base.apk` ships the Unity player data folder unencrypted. The browser shows a
`0 app UI assets` row at the top of the HOME tree; exporting it extracts every
readable UI texture/sprite (menu icons, buttons, Pokéball art, title-screen
Pikachu sheets), fonts, audio clips, and text assets — about 1,200 PNGs.

## Model export package

Export a species row with "HOME model (GLB + shiny + animations)" to get:

- `<species>.glb` — mesh + normals + embedded textures. All meshes of the form
  are merged (BodySkin + part meshes like Butterfree's FeelerSkin wings).
- `<species>_shiny.glb` — built when `_col_rare` shiny textures are cached
  (view the shiny form once in HOME to cache them).
- `textures/` — every readable `_col`/`_emi` PNG.
- `animations/*.txt` — readable AnimationClip dumps (wait/attack/roar…).
  The Cache has no Animator hierarchy, so AssetStudio cannot bind these into
  an FBX/GLB; the dumps preserve the muscle-clip data honestly.
- `home_model_export.json` — manifest with notes about anything skipped.

Material bindings (which `_col` sheet each submesh uses) are not stored in the
readable Cache — they live in the encrypted prefab. RAE assigns them
geometrically instead: tiny submeshes whose UVs land on the eye sheet's drawn
art become eyes, and every other submesh gets the body sheet whose painted
regions best match its UV islands. The same logic drives viewport previews.

## Known limits

- Per-species box icons (`cap####_*.aba`) and `mt_pv_ev` animation packages are
  UnityCN key-encrypted; RAE does not ship or brute-force keys.
- Species not viewed in HOME never reach the readable Cache and stay "locked".
