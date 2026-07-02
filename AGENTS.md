# RAE — Agent guidance

RAE (Retro Asset Extractor) under `rae/`. **ROM platforms are isolated islands** so parallel agents can work without cross-contamination.

## Read first

1. This file
2. [`docs/agents/platform-islands.md`](docs/agents/platform-islands.md) — architecture, boundaries, checklists
3. [`.cursor/rules/`](../.cursor/rules/) — repo rules matching `rae/**` (not `~/.cursor`)

## Non‑negotiable rules

1. **Platform code lives in `src/platforms/<id>/` only.** NDS nitro, HOME encryption, Unity mesh hacks stay inside that island.
2. **No cross-platform imports** unless allow-listed in `src/core/modules/platform_boundaries.py`. CI enforces this.
3. **Prefer duplicate code** over shared platform helpers. Shared neutral code: `core/`, `glb_policy/` only.
4. **UI calls `PlatformDispatch`** — do not add `from rae.platforms.nds` (or mobile/home) in `src/ui/`.
5. **Do not patch shared UI for one platform** (e.g. `texture_assigner_panel.py`). Add hooks on `ModelModule` / `ExportModule` instead.
6. **New platform → scaffold**, do not copy-paste from `nds/` by hand:
   ```bash
   cd rae && python scripts/scaffold_platform.py <id> "<Label>" --ext .rom [--active]
   ```

## Parallel agents — stay in your lane

| Agent owns | Paths | Never edit |
|------------|-------|------------|
| **NDS** | `src/platforms/nds/`, `src/easyfind/` | `platforms/mobile/`, `platforms/home/` |
| **Mobile / HOME** | `src/platforms/mobile/`, `src/platforms/home/` | `platforms/nds/`, `easyfind/` |
| **New platform** | `src/platforms/<id>/` from `_template/` | other `platforms/*` except `core/`, `glb_policy/` |
| **Core contracts** | `src/core/modules/`, `src/core/assets.py` | coordinate — affects all platforms |

Use **separate branches**. Before merge:

```bash
cd rae && python -m pytest tests/test_platform_import_isolation.py -q
```

## Module wiring

Each active platform registers `build_<id>_modules()` in `PLATFORM_MODULE_BUILDERS` (`platform_boundaries.py`).

Implement: `scan_module`, `model_module`, `texture_module`, `audio_module`, `details_module`, `export_module`.

Route preview/export through `PlatformDispatch` — never branch on `asset.magic` for platform choice in UI.

## Shared layers (OK for all agents)

| Layer | Purpose |
|-------|---------|
| `src/core/` | `Asset`, protocols, registry, dispatch, `platform_features` |
| `src/glb_policy/` | Format-neutral GLB / glTF only |
| `src/ui/` | App shell — thin; delegates to dispatch |

## Legacy shims (do not extend)

Root `src/exporter.py`, `scanner.py`, `nitro_*.py` re-export NDS code. **New code goes in `platforms/<id>/`**, not new imports of these shims.

## Testing

| Change type | Minimum test |
|-------------|----------------|
| Any `platforms/` edit | `tests/test_platform_import_isolation.py` |
| Module / dispatch | `tests/test_platform_modules.py` |
| HOME textures / mesh | `tests/test_home_textures.py`, `tests/test_mesh_preview_uvs.py` |
| GLB patch | `tests/test_texture_patch.py` |

## Skill

For platform work, invoke project skill **`rae-platform-islands`** (`.cursor/skills/rae-platform-islands/SKILL.md` in repo root).
