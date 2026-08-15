# Platform architecture

RAE separates platform-specific parsers and renderers from the application
shell. This keeps a change to one format from altering previews or exports for
another format.

The enforceable boundaries are documented in
[Platform isolation](platform-isolation.md).

## Source layout

```text
src/
  core/
    assets and platform-neutral contracts
    module registry and dispatch
  platforms/
    <id>/
      scanners and container readers
      model, texture, animation, and audio decoders
      export services
      gltf/ and glb_policy.py
      preview/material-policy.js
    _template/
      starting point for a new platform
  ui/
    Qt application shell and shared preview host
```

`src/core/` defines interfaces and dispatch. It does not interpret a console's
binary formats. Each directory under `src/platforms/` owns the complete path
from scanning through conversion and preview policy for that platform.

## Platform modules

A platform registers a `PlatformModules` object:

```python
PlatformModules(
    platform_id="<id>",
    scan=...,
    model=...,
    texture=...,
    audio=...,
    details=...,
    export=...,
    toolkit=...,  # optional
)
```

The protocols are defined in `src/core/modules/protocols.py`. Builders are
registered in `src/core/modules/platform_boundaries.py` and loaded lazily.

Asset signatures are declared by `platforms/<id>/magics.py` and collected by
`core/modules/asset_magics.py`. File-extension registration lives in
`core/registry.py`.

## UI integration

Shared UI code calls `PlatformDispatch` rather than importing a platform
implementation:

```python
from rae.core.modules import PlatformDispatch

PlatformDispatch.preview(context, asset, rom_platform_id=platform_id)
PlatformDispatch.run_export_choice(
    window,
    asset,
    choice,
    output,
    rom_platform_id=platform_id,
)
```

Optional inspector tabs are registered by the platform's model module or
`inspector.py`. Platform-specific material behavior belongs in
`platforms/<id>/preview/material-policy.js`; the shared viewer remains a host.

## Adding a platform

Create the module from the maintained template:

```bash
python scripts/scaffold_platform.py <id> "<Label>" --ext .rom --active
```

Then implement the scanner, declare asset signatures, register the module
builder and platform metadata, and add focused tests. A new exporter must set
`extras.rae.platform` on GLB output and supply its own preview material policy.

## Verification

Run the marker for the platform being changed, followed by the boundary tests:

```bash
.venv/bin/python -m pytest -m <id>
.venv/bin/python -m pytest \
  tests/test_platform_import_isolation.py \
  tests/test_platform_isolation_contract.py
```

The boundary tests and the rules in `.cursor/rules/` enforce the same module
ownership described here.
