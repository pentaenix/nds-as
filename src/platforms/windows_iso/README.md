# Windows CD/ISO platform

This module scans supported Windows disc images and exposes recognized game
assets through RAE's standard browse, preview, and export interfaces.

Read the [platform isolation rules](../../../docs/development/platform-isolation.md)
before changing its conversion or rendering behavior.

## Current format support

The initial game profile reads ISO members with `7z` and extracts InstallShield
cabinets with `unshield`. Extracted payloads are cached under
`.cache/windows_iso/`; catalog entries retain descriptors rather than complete
file contents.

The decoder currently handles:

- SMO v500 strips and legacy v50 triangle-list models;
- AM1 skinned geometry and its packed mesh records;
- AM3 scene hierarchies;
- AM2 animation sets and named clips;
- simple and detailed shadow associations;
- DDS and TGA textures converted to PNG.

Interactive previews initially load a small animation subset and reuse a
fingerprinted GLB cache. The animation inspector can prepare a complete model
with all linked animation sets when required.

The browser groups lower-detail and projected-shadow records with their primary
model. UV orientation is preserved for glTF and converted only at the COLLADA
boundary. Texture alpha is classified as opaque, cutout, or blended according
to the source data.

## Development

Platform code, GLB policy, and viewport policy live in this directory. Shared UI
code accesses the module through `PlatformDispatch`.

Run the focused suite with:

```bash
.venv/bin/python -m pytest -m windows_iso
```

Assimp and the Khronos glTF validator are useful independent compatibility
checks but are not runtime dependencies for every export path.
