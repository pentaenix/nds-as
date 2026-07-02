# {{platform_label}} — scaffolded platform island

This folder was generated from `platforms/_template/`. Each ROM platform is an **island**:
scan, decode, preview, and export live here — not in `ui/` or other platforms.

## Checklist after scaffolding

1. Implement `scan_module` — return `list[Asset]` from your ROM reader.
2. Fill `magics.py` — map asset magic bytes → `{{platform_id}}`.
3. Implement `model_module`, `texture_module`, `audio_module` preview routes.
4. Implement `export_module` — wire `export_options_for` and `run_export_choice`.
5. Register in `core/registry.py` (scaffold script does this if you pass `--active`).
6. Register builder in `core/modules/platform_boundaries.py` → `PLATFORM_MODULE_BUILDERS`.
7. Add magic loader in `core/modules/asset_magics.py` → `_{{platform_id}}_magics`.
8. Run `pytest tests/test_platform_import_isolation.py`.

## Rules

- Do **not** import other `platforms/*` packages (see `platform_boundaries.py`).
- UI calls `PlatformDispatch` only — never import this package from `ui/`.
- Prefer duplicating helpers over cross-platform imports; use `glb_policy/` for neutral GLB work.
