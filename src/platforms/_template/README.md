# {{platform_label}} — scaffolded platform island

This folder was generated from `platforms/_template/`. Each ROM platform is an **island**:
scan, decode, preview, export, **GLB policy**, and **viewport policy** live here — not in `ui/` or other platforms.

Read [`docs/agents/platform-isolation-contract.md`](../../../docs/agents/platform-isolation-contract.md) first.

## Layout (every island)

```
platforms/{{platform_id}}/
  glb.py or service.py       # export writer
  glb_policy.py              # material renderClass / extras.rae (platform-owned)
  preview/
    material-policy.js       # WebEngine viewport policy (platform-owned)
  inspector.py               # optional inspector tab registration
  model_module/ …            # preview routes; pass preview policy URL to viewer
  tests/ (via pytest -m {{platform_id}})
```

## Checklist after scaffolding

1. Implement `scan_module` — return `list[Asset]` from your ROM reader.
2. Fill `magics.py` — map asset magic bytes → `{{platform_id}}`.
3. Implement `model_module`, `texture_module`, `audio_module` preview routes.
4. Implement `export_module` — wire `export_options_for` and `run_export_choice`.
5. Implement `glb_policy.apply_platform_glb_policy` — set `extras.rae.platform = "{{platform_id}}"`.
6. Implement `preview/material-policy.js` — fork from another island if needed; **do not** edit shared `ui/preview/static/rae-material-policy.js`.
7. Register in `core/registry.py` (scaffold script does this if you pass `--active`).
8. Register builder in `core/modules/platform_boundaries.py` → `PLATFORM_MODULE_BUILDERS`.
9. Add magic loader in `core/modules/asset_magics.py` → `_{{platform_id}}_magics`.
10. Run `pytest tests/test_platform_import_isolation.py tests/test_platform_isolation_contract.py`.
11. Run `pytest -m {{platform_id}}` only for day-to-day work on this island.

## Rules

- Do **not** import other `platforms/*` packages (see `platform_boundaries.py`).
- UI calls `PlatformDispatch` only — never import this package from `ui/`.
- **Duplicate** helpers over cross-platform imports.
- Shared `glb_policy/` is **glTF I/O only** — not material classification for this console.
- Changing this island’s viewport must not require editing another platform’s files.
