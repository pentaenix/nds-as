"""Backward-compatible re-export; mobile mesh export lives in platforms/mobile."""
from ..mobile.mesh_export import (  # noqa: F401
    UnityMeshPreviewResult,
    export_first_mesh_preview_glb,
    unitypy_available,
    _obj_uv_to_gltf,
    _parse_obj,
    _safe_name,
    _split_obj_by_groups,
    _write_glb,
    _write_glb_grouped,
)
