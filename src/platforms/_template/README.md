# {{platform_label}} platform template

This directory is the starting point for a platform module. Platform-specific
scanning, decoding, preview behavior, and export policy should remain within the
generated module.

Read the [platform isolation rules](../../../docs/development/platform-isolation.md)
before implementing a new platform.

## Layout

```text
platforms/{{platform_id}}/
  platform.py
  platform_modules.py
  rom.py
  scan_module/
  model_module/
  texture_module/
  audio_module/
  details_module/
  export_module/
  gltf/
  glb_policy.py
  preview/material-policy.js
```

Register the module through `core/modules/platform_boundaries.py`, keep the UI
integration behind `PlatformDispatch`, and run the platform marker plus the
import-boundary tests after implementation.
