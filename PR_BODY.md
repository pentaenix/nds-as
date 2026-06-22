## Summary

Adds an initial Android/Unity/Pokémon HOME source backend so RAE can scan mobile HOME cache/package folders and build grouped Pokémon form package inventories.

## Included

- `rae home scan/list/report` CLI family
- Android APK/APKM/XAPK/OBB/folder scanner
- Unity magic detection and optional UnityPy object inventory
- HOME `pm####_##_##` / `pkm_####` recognizer
- Pokémon display-name seed mapping, including `pkm_54 -> Psyduck`
- HOME package grouping with model/texture/rig/animation completeness status
- Animation clip classifier for idle / physical attack / special attack candidate names
- UI File-menu entry: `Open Pokémon HOME Source…`
- Details panel rendering for HOME package rows
- Mobile mapping file: `mappings/mobile/pokemon_home.json`
- Documentation: `docs/pokemon_home_mobile_assets.md`

## Safety boundary

This patch does not include decryption keys, server scraping, runtime hooks, or protected asset bypass. `.aba/.abap` files are detected and reported as unsupported unless the user supplies readable local cache/bundle files.

## Test plan

```bash
python -m compileall src
./rae home scan /path/to/home/source --out exports/home_inventory.json
./rae home list /path/to/home/source
./rae home report exports/home_inventory.json
```

Optional:

```bash
.venv/bin/python -m pip install UnityPy
./rae home scan /path/to/readable/unity/bundles --out exports/home_inventory_unitypy.json
```
