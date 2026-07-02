# RAE platform islands

Canonical reference for agents and developers. Enforced by `tests/test_platform_import_isolation.py` and `.cursor/rules/rae-*.mdc`.

## Mental model

```
rae/src/
  core/              contracts, Asset, PlatformDispatch, registry
  glb_policy/        glTF/GLB only — no Nintendo or Unity logic
  platforms/
    nds/             Nintendo DS / Nitro island
    mobile/          Mobile .rom island
    home/            HOME sub-layer (mobile only)
    unity/           Legacy shim → mobile mesh_export
    _template/       Scaffold source (not valid Python until expanded)
    stub/            Not-implemented platform modules
  ui/                Qt shell — must use PlatformDispatch
  easyfind/          NDS-only map tool (gated in UI)
```

Each island owns: scan, decode, preview, export, format-specific hacks.

## Import rules

Hard bans and allow-list live in `src/core/modules/platform_boundaries.py`.

| From | May import |
|------|------------|
| `platforms/nds` | `core/`, `glb_policy/`, self |
| `platforms/mobile` | `core/`, `glb_policy/`, `home/`, `unity/`, self |
| `platforms/home` | `core/`, `glb_policy/`, `mobile/`, `unity/`, `android/`, self |
| `platforms/gba`, `gb`, etc. | `core/`, `glb_policy/`, self |

**Forbidden:** `nds` ↔ `mobile`/`home`, `gba` → `nds`, etc.

When unsure: duplicate the helper under your platform instead of importing another.

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

Checklist:

- [ ] `scan_<id>_rom_path` in `rom.py`
- [ ] `ASSET_MAGICS` in `magics.py`
- [ ] Preview routes use neutral `PreviewRoute` (`MODEL`, `TEXTURE_2D`, …)
- [ ] `export_module` implements folder + single-asset export
- [ ] `PLATFORM_MODULE_BUILDERS["<id>"]` registered
- [ ] `core/registry.py` — `Platform` entry
- [ ] `pytest tests/test_platform_import_isolation.py`

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
```

NDS-only features (EasyFind, texture library warmup) are gated via `core/platform_features.py`.

## Parallel agent workflow

1. Pick one island (or `core/modules` with team agreement).
2. Branch from `main`.
3. Edit only paths in your lane (see `rae/AGENTS.md`).
4. Run isolation test before PR.
5. Avoid drive-by edits to `ui/main/shell.py` or other NDS-heavy mixins unless your task is explicitly UI routing.

## What is not yet fully isolated

- `ui/main/*.py` mixins still import root NDS shims (`exporter`, `nitro_*`) — historical debt.
- Export smart flow is dispatched; some menu actions (`export_selected`) still use NDS shims directly.

When touching UI, **move logic into platform modules** and leave a thin dispatch call — do not add more NDS imports.

## Related files

| File | Role |
|------|------|
| `core/modules/dispatch.py` | Router |
| `core/modules/platform_boundaries.py` | Import rules + `PLATFORM_MODULE_BUILDERS` |
| `platforms/_template/` | New platform skeleton |
| `scripts/scaffold_platform.py` | Generator |
| `.cursor/skills/rae-platform-islands/SKILL.md` | Agent workflow skill |
