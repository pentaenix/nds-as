# Platform isolation

Platform-specific behavior in RAE is intentionally self-contained. Nintendo DS
conversion code, for example, must not change Nintendo 3DS or Windows previews.
The test suite enforces the import rules described below.

## Ownership

| Area | Responsibility |
|------|----------------|
| `platforms/<id>/` | Container parsing, decoding, conversion, and export |
| `platforms/<id>/gltf/` | GLB I/O and platform-specific material processing |
| `platforms/<id>/glb_policy.py` | Public entry point for that GLB policy |
| `platforms/<id>/preview/` | Platform-specific viewport behavior |
| `core/` | Platform-neutral types, protocols, registry, and dispatch |
| `ui/` | Shared Qt shell and preview host |

When two platforms need similar format code, keep independent implementations
unless the abstraction is demonstrably platform-neutral. This is deliberate:
material classification and binary-format assumptions often diverge even when
the resulting files look similar.

## Import boundaries

The rules are implemented in `src/core/modules/platform_boundaries.py` and
verified by `tests/test_platform_import_isolation.py`.

1. A platform module may import `core` and its own package.
2. A platform module may not import `ui`.
3. Shared UI code may not directly import a platform implementation; it uses
   `PlatformDispatch`.
4. Cross-platform imports are rejected unless listed explicitly in
   `ALLOWED_CROSS_PLATFORM`.
5. Platform-specific helpers do not belong in a `platforms/shared` package.

The mobile, HOME, Unity, and Android packages contain a small documented
allow-list because they form one application-archive pipeline.

## GLB metadata and material policy

Each active platform owns its GLB writer and material classifier. Exported GLB
materials use the `extras.rae` namespace to identify the originating policy:

```json
{
  "extras": {
    "rae": {
      "platform": "nds",
      "schemaVersion": 1,
      "renderClass": "opaque"
    }
  }
}
```

Fields that describe a particular renderer or source format remain in that
platform's schema. They are not added to a shared classifier merely because
another platform also exports GLB.

The platform-local `gltf/` package normally contains:

| Module | Purpose |
|--------|---------|
| `classify.py`, `apply.py` | Material classification and metadata |
| `glb_io.py` | GLB parsing and serialization |
| `texture_patch.py`, `embed_textures.py` | Texture repair and embedding |
| `preview_textures.py` | Texture-to-mesh preview mapping |
| `merge_animations.py`, `platform_animation.py` | Animation helpers |

## Preview policy

`ui/preview/static/glb_viewer.html` provides loading, camera control, and the
animation loop. It must remain independent of console formats.

Material classification, culling, eye layers, texture transforms, and other
format-specific behavior belong in
`platforms/<id>/preview/material-policy.js`. The platform model module supplies
the policy URL when opening the viewer.

## Tests

Use the marker that matches the changed platform:

| Change | Tests |
|--------|-------|
| `platforms/nds/**` | `pytest -m nds` |
| `platforms/threeds/**` | `pytest -m threeds` |
| `platforms/windows_iso/**` | `pytest -m windows_iso` |
| `platforms/mobile/**` | `pytest -m mobile` |
| shared dispatch or protocols | `pytest -m core_shared` |

Every platform change also runs:

```bash
.venv/bin/python -m pytest \
  tests/test_platform_import_isolation.py \
  tests/test_platform_isolation_contract.py
```

Small representative fixtures belong under `tests/fixtures/golden/<id>/` when
a byte-level or visual regression check is appropriate.

## Existing exceptions

Two shared-UI imports predate the current boundary and are tracked by the
contract tests:

| Location | Planned cleanup |
|----------|-----------------|
| `ui/workers/preview.py` | Move the remaining Nintendo DS type dependency into `core` |
| `ui/main/threeds_panel.py` | Complete migration to the 3DS inspector module |

New code should not add to this exception list.

## Related documentation

- [Platform architecture](platform-architecture.md)
- [Nintendo DS GLB rendering policy](../GLB_RENDER_POLICY.md)
