# RAE platform isolation contract (hard rules)

**Canonical.** Agents and humans must follow this before any change under `rae/`.
Violations are CI failures or review blockers unless explicitly listed in
[Known debt](#known-debt-migration-in-progress).

## Goal

**True separation:** work on Nintendo DS GLB export must not affect Nintendo 3DS
preview, viewport, or export — and vice versa. The same for GBA, Switch, and
every future island. You run **only your platform’s tests** for day-to-day work.

Import isolation alone is **not** sufficient. Shared classifiers, shared viewer
material policy, and shared `extras.rae` semantics caused cross-platform
regressions. This contract forbids that pattern going forward.

## Four rings (what may be shared)

```
Ring 0  platforms/<id>/          scan, decode, export, GLB writer, tests — ISLAND
Ring 1  platforms/<id>/glb_policy.py, extras.rae semantics — ISLAND
Ring 2  platforms/<id>/preview/  material-policy.js, viewer hooks — ISLAND
Ring 3  ui/ + core/modules/      Qt shell + dispatch only — SHARED SPINE
```

| Ring | Owner | May import from |
|------|--------|-----------------|
| 0–2 | One `platforms/<id>/` | `core/`, `glb_io/` helpers (see below), self |
| 3 `ui/` | App shell | `core/`, `PlatformDispatch` — **not** `platforms.nds`, `platforms.threeds`, … |
| 3 `core/` | Contracts | Neutral types only — **no** Nitro, GF, PICA, apicula logic |

**Rule:** If it encodes how *one* console renders or exports models, it lives in
that console’s island (Rings 0–2). **Duplicate code on purpose.**

## GLB and `extras.rae`

### Per-platform ownership

Each active platform **must** own:

- `platforms/<id>/glb.py` (or equivalent) — geometry/textures/animations export
- `platforms/<id>/glb_policy.py` — material classification, `renderClass`, alpha
  modes, platform-specific `extras.rae` fields
- `platforms/<id>/preview/material-policy.js` — WebEngine viewport material/eye
  behavior for **that** platform only

On export, set:

```json
"extras": {
  "rae": {
    "platform": "nds",
    "schemaVersion": 1,
    "renderClass": "opaque"
  }
}
```

GF 3DS eyes, mesh visibility, incandescent overlays, etc. are **`3ds` schema
fields** — never added to shared classifiers or shared viewer policy.

### GLB stack per island (`platforms/<id>/gltf/`)

There is **no** top-level `glb_policy/` package. Each platform duplicates the full
GLB stack under `platforms/<id>/gltf/`:

| Module | Purpose |
|--------|---------|
| `classify.py`, `apply.py` | Material `renderClass`, `extras.rae.platform` |
| `glb_io.py`, `texture_patch.py`, `embed_textures.py` | Read/write GLB bytes |
| `preview_textures.py` | Mesh/texture maps for preview |
| `merge_animations.py`, `platform_animation.py` | Animation helpers |

**New platform:** copy `gltf/` from `_template` or fork another island. Wire
`apply_platform_glb_policy()` in `platforms/<id>/glb_policy.py` to call local
`gltf.apply`.

## Viewport / WebEngine preview

### One material policy per platform

- **Shell:** `ui/preview/static/glb_viewer.html` — camera, load GLB, animation loop
  only. **No** per-console blend/eye logic in the shell.
- **Policy:** `platforms/<id>/preview/material-policy.js` — duplicated per island.
  Fork from another platform if needed; **do not** edit a shared policy file for
  one console.

Model module passes the policy URL when opening the viewport (query param or
sidecar). Changing GBA viewport policy edits **only**
`platforms/gba/preview/material-policy.js`.

### Forbidden

- Adding `if (platform === '3ds')` (or magic/material-name hacks) to the shared
  `glb_viewer.html` shell for new behavior
- Putting GF eye-sheet logic in the shared viewer shell
- Importing `material-policy.js` from platform Python (policy is loaded by
  the viewer URL, not Python imports)

## UI, commands, inspector tabs

- **Dispatch only:** `PlatformDispatch.preview`, `.run_export_choice`, etc.
- **No** `from rae.platforms.nds...` in `ui/` (see known debt).
- Platform inspector tabs (3DS animations, GBA tile maps, etc.) register via the
  platform **model module** or `platforms/<id>/inspector.py` — not by editing
  unrelated mixins.
- Adding/removing Switch UI must not require editing `threeds_panel.py` or NDS
  preview workers.

## Python import rules

Enforced by `tests/test_platform_import_isolation.py` and
`tests/test_platform_isolation_contract.py`.

1. **No cross-platform imports** under `platforms/` (allow-list: mobile↔home only).
2. **Platforms must not import `ui/`**.
3. **`ui/` must not import `platforms.<id>.*`** except debt listed below; debt
   must not grow without updating the baseline in the test file.
4. **No `platforms/shared/`** package — ever.
5. **Scaffold new consoles** from `platforms/_template/` — never copy `nds/` by hand.

## Tests (run only what you touch)

Pytest markers: `nds`, `threeds`, `gba`, `switch`, `mobile`, `core_shared`.

| You changed | Run |
|-------------|-----|
| `platforms/nds/**` only | `pytest -m nds` + import/contract tests |
| `platforms/threeds/**` only | `pytest -m threeds` + import/contract tests |
| `platforms/<new>/**` only | `pytest -m <new>` + import/contract tests |
| `platforms/<id>/gltf/classify.py` or `apply.py` | `pytest -m <id>` for that island |
| `ui/preview/static/glb_viewer.html` (shell only) | `core_shared` viewer smoke |
| `core/modules/dispatch.py` or `protocols.py` | `core_shared` + import/contract tests |

Each island keeps **small golden exports** under `tests/fixtures/golden/<id>/`
(added per platform as needed). No daily full-matrix runs for island-only edits.

## Agent checklist (every platform task)

```
- [ ] Files under platforms/<my-id>/ only (or core/ with team agreement)
- [ ] No cross-platform imports
- [ ] GLB policy in platforms/<my-id>/glb_policy.py — not shared classify
- [ ] Viewport policy in platforms/<my-id>/preview/material-policy.js
- [ ] extras.rae.platform set on export
- [ ] UI via PlatformDispatch / inspector registration — no platform imports in ui/
- [ ] pytest -m <my-id> and test_platform_isolation_contract.py
```

## Known debt (migration in progress)

| Location | Status |
|----------|--------|
| `platforms/nds/gltf/` | **Done** — NDS GLB I/O + classify (pre-3DS DS rules) |
| `platforms/threeds/gltf/` | **Done** — 3DS GLB I/O + classify (GF/additive/ETC1A4) |
| `platforms/mobile/gltf/`, `platforms/home/gltf/` | **Done** — island-owned GLB stack |
| `platforms/gba`, `gb`, `gbc`, `switch` `gltf/` | **Done** — scaffold copies per island |
| `src/glb_policy/` top-level | **Removed** — use `platforms/<id>/gltf/` |
| `ui/preview/static/rae-material-policy.js` | **Removed** — viewer loads `?policy=` per platform |
| `ui/workers/preview.py` | Imports `platforms.nds.nitro.types` — move to `core/` |
| `ui/main/threeds_panel.py` | Move to `platforms/threeds/inspector.py` |

New work **must not** extend legacy paths for NDS/3DS behavior.

## Related

- [`platform-islands.md`](platform-islands.md) — structure and scaffolding
- [`../GLB_RENDER_POLICY.md`](../GLB_RENDER_POLICY.md) — NDS `renderClass` semantics (nds-owned)
- `rae/AGENTS.md` — agent entry
- `.cursor/rules/rae-*.mdc` — Cursor enforcement
