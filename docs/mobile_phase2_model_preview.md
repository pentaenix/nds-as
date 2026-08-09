# Mobile phase 2: first model rendering

This patch adds the first renderable mobile-model path for the `mobile` branch.

## Goal

Phase 2 is intentionally narrow:

- select a `UNITY` asset row or a `HOME` package row
- build a geometry-only preview from the first previewable Unity `Mesh`
- emit a `.glb` under `exports/mobile_model_previews/`
- show the GLB in RAE if a compatible preview hook exists, or open it externally as a fallback

Textures and animation playback are explicitly deferred to later phases.

## Requirements

Install `UnityPy` in the RAE virtual environment:

```bash
python -m pip install UnityPy
```

## UI

- `Device Toolkit → Mobile → Preview Selected Mobile Model`
- toolbar fallback: `Preview Mobile Model`

## Notes

- this phase relies on UnityPy's mesh export path and converts the exported OBJ into a minimal GLB
- this is intended as a first on-ramp for model visibility, not a final import pipeline
