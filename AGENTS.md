# RAE — Agent guidance

RAE (Retro Asset Extractor) under `rae/`. **ROM platforms are isolated islands** so parallel agents can work without cross-contamination.

## Read first

1. This file
2. [`docs/agents/platform-isolation-contract.md`](docs/agents/platform-isolation-contract.md) — **hard rules** (GLB, viewport, tests)
3. [`docs/agents/platform-islands.md`](docs/agents/platform-islands.md) — structure, scaffolding, lanes
4. [`.cursor/rules/`](../../.cursor/rules/) — repo-root `rae-*.mdc` (not `~/.cursor`)

## Non‑negotiable rules

1. **Platform code lives in `src/platforms/<id>/` only.** Nitro, GF/PICA, HOME, Switch hacks stay inside that island.
2. **No cross-platform imports** unless allow-listed in `src/core/modules/platform_boundaries.py`. CI enforces this.
3. **Prefer duplicate code** over shared platform helpers. **Do not** put console-specific logic in a shared top-level GLB package or shared viewer material policy.
4. **Each island owns:**
   - `platforms/<id>/gltf/` — full GLB I/O + material classification
   - `platforms/<id>/glb_policy.py` — thin entry that calls local `gltf.apply`
   - `platforms/<id>/preview/material-policy.js` — WebEngine viewport for that console only
5. **UI calls `PlatformDispatch`** — do not add `from rae.platforms.nds` (or mobile/home/threeds) in `src/ui/`.
6. **Do not patch shared UI/viewport for one platform** (e.g. `texture_assigner_panel.py`, `glb_viewer.html`). Use island `inspector.py`, `model_module`, or `preview/material-policy.js`.
7. **New platform → scaffold**, do not copy-paste from `nds/` by hand:
   ```bash
   cd rae && python scripts/scaffold_platform.py <id> "<Label>" --ext .rom [--active]
   ```

## Tests (run only your island)

| You edit | Run |
|----------|-----|
| `platforms/nds/**` | `pytest -m nds` + `tests/test_platform_isolation_contract.py` |
| `platforms/threeds/**` | `pytest -m threeds` + contract tests |
| `platforms/<id>/**` | `pytest -m <id>` + contract tests |
| `platforms/<id>/gltf/**` | `pytest -m <id>` + contract tests |

```bash
cd rae && python -m pytest tests/test_platform_import_isolation.py tests/test_platform_isolation_contract.py -q
```

## Parallel agents — stay in your lane

| Agent owns | Paths | Never edit |
|------------|-------|------------|
| **NDS** | `src/platforms/nds/`, `src/easyfind/` | other `platforms/*` |
| **3DS** | `src/platforms/threeds/` | `platforms/nds/`, `easyfind/` |
| **Mobile / HOME** | `src/platforms/mobile/`, `src/platforms/home/` | `platforms/nds/`, `easyfind/` |
| **Switch / GBA / new** | `src/platforms/<id>/` from `_template/` | other `platforms/*` except `core/` |
| **Core contracts** | `src/core/modules/`, `src/core/assets.py` | coordinate — affects all platforms |

Use **separate branches**. Island-only PRs must not require running other platforms’ test suites.

## Shared layers (minimal)

| Layer | Purpose |
|-------|---------|
| `src/core/` | `Asset`, protocols, registry, `PlatformDispatch` |
| `src/ui/preview/static/glb_viewer.html` | Viewer **shell** — no per-console material logic |

**Not shared:** `gltf/` stacks, material classification, eye sheets, mesh visibility, viewport blend rules.

## Module wiring

Each active platform registers `build_<id>_modules()` in `PLATFORM_MODULE_BUILDERS` (`platform_boundaries.py`).

Implement: `scan_module`, `model_module`, `texture_module`, `audio_module`, `details_module`, `export_module`.

Route preview/export through `PlatformDispatch` — never branch on `asset.magic` for platform choice in UI.

## Legacy shims (do not extend)

Root `src/exporter.py`, `scanner.py`, `nitro_*.py` re-export NDS code. **New code goes in `platforms/<id>/`**, not new imports of these shims.

## Skill

For platform work, invoke project skill **`rae-platform-islands`** (`.cursor/skills/rae-platform-islands/SKILL.md` in repo root).
