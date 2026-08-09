# Windows CD/ISO viewport material policy

This folder is **platform-owned** (Ring 2). Changes here affect only
`windows_iso` preview — not NDS, 3DS, or any other island.

## Files

- `material-policy.js` — WebEngine three.js material/texture policy for this console.

## Wiring

`model_module` must pass this policy URL when loading the shared viewer shell
(`ui/preview/static/glb_viewer.html`). Do **not** edit the shared
`rae-material-policy.js` for windows_iso-specific behavior.

## Rule

When unsure, **duplicate** from another platform’s `preview/material-policy.js`
into this folder — never share one policy file across consoles.
