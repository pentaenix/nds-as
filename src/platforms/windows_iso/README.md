# Windows CD/ISO platform island

This folder was generated from `platforms/_template/`. Each ROM platform is an **island**:
scan, decode, preview, export, **GLB policy**, and **viewport policy** live here — not in `ui/` or other platforms.

Read [`docs/agents/platform-isolation-contract.md`](../../../docs/agents/platform-isolation-contract.md) first.

## First supported disc

Marine Park Empire (Windows, 2005) is identified from ISO members rather than
its filename. RAE uses `7z` to read the ISO and `unshield` to catalog/extract
the InstallShield cabinets. Cabinet payloads are cached under
`.cache/windows_iso/`; browser rows contain metadata descriptors, not raw game
files.

The clean-room format path supports:

- SMO v500 strips and the legacy v50 triangle-list buildings;
- AM1 skinned geometry, including packed unaligned mesh records and retained
  zero/small-weight influences; AM1's eight in-header strip setup indices are
  retained so the first two triangles of each mesh are not lost;
- AM3 hierarchies;
- every AM2 set named by `MODEL.RES` and `AnimGrp.res` as separate GLB/DAE clips;
- authoritative simple/detail shadow associations and DDS/TGA-to-PNG textures.

Interactive previews intentionally load the primary AM2 bank and its first four
clips, then reuse a fingerprinted GLB cache on later selections. The animation
inspector's **Load all animations** action builds and caches one GLB containing
every linked AM2 bank. Complete GLB, DAE, folder, and Models Resource exports do
the same. AM2 action timestamps are circular end markers, so each marker closes
the preceding named action.

The browser folds `#2`/`#3` render LODs and `#s` projected-shadow meshes into
the unsuffixed primary model row. V3D's top-left UVs are retained for glTF and
converted only at the COLLADA boundary. Multi-part AM1 meshes share a lone
declared texture when appropriate. Texture alpha is classified as opaque,
compression-noisy cutout, or genuinely blended instead of forcing all materials
through depth-sorted transparency.

The Models Resource export writes adjacent `<Readable Name>.zip`,
`<Readable Name>_icon.png`, `<Readable Name>_preview.glb`, and
`<Readable Name>_preview.png` files under `exports/Marine Park Empire/`. The ZIP
contains COLLADA plus external PNG textures only. The preview GLB embeds its
textures and every animation, with the source idle clip first when supplied by
the game. Icons are 148x125 and large previews are 750x650, both RGBA with a
transparent background.

Known non-model records are excluded from the model browser: 64 16-byte SMO
sentinels, null geometry, and the special `wave.AM1` effect record.

## Layout

```
platforms/windows_iso/
  glb.py or service.py       # export writer
  glb_policy.py              # material renderClass / extras.rae (platform-owned)
  preview/
    material-policy.js       # WebEngine viewport policy (platform-owned)
  inspector.py               # optional inspector tab registration
  model_module/ …            # preview routes; pass preview policy URL to viewer
  tests/ (via pytest -m windows_iso)
```

Run `pytest -m windows_iso` for the island suite. Assimp and the Khronos glTF
validator are useful independent release checks, but are not runtime
dependencies of the exporter.

## Rules

- Do **not** import other `platforms/*` packages (see `platform_boundaries.py`).
- UI calls `PlatformDispatch` only — never import this package from `ui/`.
- **Duplicate** helpers over cross-platform imports.
- Shared `glb_policy/` is **glTF I/O only** — not material classification for this console.
- Changing this island’s viewport must not require editing another platform’s files.
