# RAE — Retro Asset Extractor

**RAE** (Retro Asset Extractor) is a local desktop app and CLI for exploring **your own legally dumped ROMs** and exporting readable assets. Nintendo DS is fully supported today; Game Boy, Game Boy Color, Game Boy Advance, and Nintendo 3DS are scaffolded for future work.

RAE does **not** download ROMs, bypass copy protection, or ship extracted copyrighted assets.

## Platform status

| Platform | Folder | ROM extensions | Status |
|----------|--------|----------------|--------|
| Nintendo DS | `src/platforms/nds/` | `.nds` | **Active** — scan, preview, export |
| Game Boy Advance | `src/platforms/gba/` | `.gba` | Planned |
| Game Boy Color | `src/platforms/gbc/` | `.gbc` | Planned |
| Game Boy | `src/platforms/gb/` | `.gb` | Planned |
| Nintendo 3DS | `src/platforms/threeds/` | `.3ds`, `.cci`, `.cxi` | Planned |

## Project layout

```text
src/
  platforms/
    nds/          DS ROM scan, Nitro decoders, audio, model export
    mobile/       HOME / .rom mobile app support
    home/         Pokémon HOME implementation (used via mobile)
    android/      Android device source helpers
    gba/ gbc/ gb/ threeds/   stubs + README (contributions welcome)
  core/           mappings, platform registry, modules dispatch
  ui/             desktop app (Qt)
  cli/            command-line tools
mappings/
  nds/            community DS game mappings (PRs welcome)
  gba/ gbc/ gb/ 3ds/   reserved for future mappings
roms/             your ROM dumps (git-ignored)
exports/          extracted output (git-ignored)
saves/            RAE session files (git-ignored)
```

## Community mappings

Mappings are **shared community data only** — path labels, categories, search presets, and relationship hints. They live under `mappings/<platform>/` and are loaded automatically. There are no personal override folders; improve mappings via pull request so everyone benefits.

See `mappings/README.md` for contribution guidelines.

### TODO: mapping authoring UI

A planned next step is an in-app **mapping editor** so contributors can draft archive labels and search presets visually, then export JSON for a PR. CI will validate mapping files against `mappings/schema.json`.

## Install

```bash
./rae install
./rae run
```

Windows: `rae.bat install` then `rae.bat run`

The legacy `./dsas` launcher still works but forwards to `./rae`.

## Nintendo DS workflow (today)

1. Put your `.nds` dump in `roms/`.
2. Run `./rae run` and open the ROM.
3. Browse mapped folders, Raw Folders, or filter (`BMD0`, `BTX0`, `cat:models`, …).
4. Preview assets; for models use **Set Textures** for verified material→texture binding.
5. **Export Selected…** for raw, readable PNG, GLB (via apicula), or audio bundles.
6. **Save Session** (`.raesession`) to continue without rescanning.

## CLI examples

```bash
./rae info roms/game.nds
./rae list roms/game.nds --query BMD0
./rae decode roms/game.nds --out exports/readable --query BTX0
./rae convert roms/game.nds --out exports/models --query BMD0 --format glb
./rae audio roms/game.nds --out exports/audio
./rae mappings
```

## Optional apicula (DS models)

Vendored Rust tool under `tools/apicula/`. `./rae install` builds it when Cargo is available.

RAE looks for apicula on `PATH`, `RAE_APICULA` (legacy: `DSAS_APICULA`, `DSM_APICULA`), and `tools/apicula/target/release/apicula`.

After apicula converts a model to GLB, RAE runs a **NDS-owned** material policy pass (`platforms/nds/gltf/apply.py`) that writes `extras.rae.renderClass` on each material. See `docs/GLB_RENDER_POLICY.md` and `docs/agents/platform-isolation-contract.md`.

3D preview uses **Qt WebEngine + three.js**. Each platform uses its own `platforms/<id>/preview/material-policy.js` (loaded via `?policy=` in the viewer URL). Install PySide6 Add-ons if WebEngine is missing. Set `RAE_LEGACY_GL_PREVIEW=1` to fall back to the old pyqtgraph path.

## Agents and contributors

ROM platforms are **isolated islands** under `src/platforms/<id>/` — each owns GLB policy and viewport policy.

- **Hard contract:** [`docs/agents/platform-isolation-contract.md`](docs/agents/platform-isolation-contract.md)
- **Agents:** [`AGENTS.md`](AGENTS.md) and [`docs/agents/platform-islands.md`](docs/agents/platform-islands.md)
- **Cursor rules (this repo):** `.cursor/rules/rae-*.mdc` at the monorepo root
- **Skill:** `.cursor/skills/rae-platform-islands/`
- **New platform:** `python scripts/scaffold_platform.py <id> "<Label>" --ext .rom [--active]`
- **CI guards:** `pytest tests/test_platform_import_isolation.py tests/test_platform_isolation_contract.py`
- **Day-to-day:** `pytest -m <your-platform-id>` only

## Development

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -p 'test_*.py' -q
```

Python package name: `rae`. The `dsm` import path remains as a deprecated alias for compatibility.
