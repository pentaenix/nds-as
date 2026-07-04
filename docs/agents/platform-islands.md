# RAE platform islands

Canonical reference for agents and developers.

**Hard rules (GLB, viewport, tests):** [`platform-isolation-contract.md`](platform-isolation-contract.md)

Enforced by:

- `tests/test_platform_import_isolation.py`
- `tests/test_platform_isolation_contract.py`
- `.cursor/rules/rae-*.mdc`

## Mental model — four rings

```
Ring 0  platforms/<id>/           scan, decode, export, format hacks
Ring 1  platforms/<id>/gltf/ + glb_policy.py   material / extras.rae (per island)
Ring 2  platforms/<id>/preview/   material-policy.js (per island viewport)
Ring 3  core/ + ui/               dispatch + Qt shell only
```

Each island owns Rings 0–2. **Duplicate** GLB policy and viewport policy per console — do not extend shared classifiers for one platform.

```
rae/src/
  core/              contracts, Asset, PlatformDispatch, registry
  platforms/
    nds/             Nintendo DS / Nitro island (+ gltf/, preview/)
    threeds/         Nintendo 3DS / GF island
    mobile/          Mobile .rom island
    home/            HOME sub-layer (mobile only)
    switch/          Nintendo Switch (scaffold)
    _template/       Scaffold source — gltf/ + preview stubs
    stub/            Not-implemented platform modules
  ui/                Qt shell — PlatformDispatch; viewer shell only in static/
  easyfind/          NDS-only map tool (gated in UI)
```

## Import rules

Hard bans and allow-list live in `src/core/modules/platform_boundaries.py`.

| From | May import |
|------|------------|
| `platforms/nds` | `core/`, self |
| `platforms/threeds` | `core/`, self |
| `platforms/mobile` | `core/`, `home/`, `unity/`, self |
| `platforms/home` | `core/`, `mobile/`, `unity/`, `android/`, self |
| `platforms/gba`, `gb`, `switch`, etc. | `core/`, self |

**Forbidden:** `nds` ↔ `threeds`, `gba` → `nds`, `platforms/*` → `ui/`, `ui/` → `platforms.nds` (see contract debt list).

When unsure: **duplicate** the helper under your platform instead of importing another.

## Platform module surface

```python
# platforms/<id>/platform_modules.py
PlatformModules(
    platform_id="<id>",
    scan=...,
    model=...,
    texture=...,
    audio=...,
    details=...,
    export=...,      # required
    toolkit=...,     # optional
)
```

Protocols: `src/core/modules/protocols.py`

Asset magic → platform: `platforms/<id>/magics.py` merged in `core/modules/asset_magics.py`.

## Adding a platform

```bash
cd rae
python scripts/scaffold_platform.py switch "Nintendo Switch" --ext .nsp --ext .xci --active
```

Scaffold creates `glb_policy.py`, `preview/material-policy.js`, and `inspector.py` stubs.

Checklist:

- [ ] `scan_<id>_rom_path` in `rom.py`
- [ ] `ASSET_MAGICS` in `magics.py`
- [ ] `glb_policy.apply_platform_glb_policy` — set `extras.rae.platform`
- [ ] `preview/material-policy.js` — viewport policy for this island only
- [ ] Preview routes use neutral `PreviewRoute` (`MODEL`, `TEXTURE_2D`, …)
- [ ] `export_module` implements folder + single-asset export
- [ ] `PLATFORM_MODULE_BUILDERS["<id>"]` registered
- [ ] `core/registry.py` — `Platform` entry
- [ ] `pytest -m <id>` and contract tests

## UI integration

Correct:

```python
from rae.core.modules import PlatformDispatch

PlatformDispatch.preview(ctx, asset, rom_platform_id=self._rom_platform_id)
PlatformDispatch.run_export_choice(self, asset, choice, out, rom_platform_id=...)
```

Incorrect:

```python
from rae.platforms.nds.exporter import convert_with_apicula  # in ui/
# Editing platforms/gba/preview/material-policy.js for GBA-only behavior
# Adding GF eye logic to platforms/nds/gltf/classify.py
```

Platform inspector tabs: `platforms/<id>/inspector.py` + model module registration.

NDS-only features (EasyFind, texture library warmup) are gated via `core/platform_features.py`.

## Parallel agent workflow

1. Pick one island (or `core/modules` with team agreement).
2. Branch from `main`.
3. Edit only paths in your lane (see `rae/AGENTS.md`).
4. Run `pytest -m <your-id>` + contract tests — **not** other platforms’ suites.
5. Avoid drive-by edits to shared rings unless intentional.

## Known debt (do not extend)

See [`platform-isolation-contract.md`](platform-isolation-contract.md#known-debt-migration-in-progress). Top-level `glb_policy/` and shared `rae-material-policy.js` are removed — each island owns `gltf/` and `preview/material-policy.js`.

## Related files

| File | Role |
|------|------|
| `docs/agents/platform-isolation-contract.md` | Hard isolation contract |
| `core/modules/dispatch.py` | Router |
| `core/modules/platform_boundaries.py` | Import rules + `PLATFORM_MODULE_BUILDERS` |
| `platforms/_template/` | New platform skeleton |
| `scripts/scaffold_platform.py` | Generator |
| `.cursor/skills/rae-platform-islands/SKILL.md` | Agent workflow skill |
