"""OpenGL draw-state helpers for the NDS model preview."""
from __future__ import annotations

from OpenGL import GL

from ...glb_preview_textures import MaterialPreviewState
from pyqtgraph.opengl.GLGraphicsItem import GLOptions

# Preview re-orients apicula +Y into +Z via (x, y, z) -> (x, z, y), which flips
# triangle winding. DS/apicula treat clockwise faces as culled, so use CW fronts.
_PREVIEW_FRONT_FACE = GL.GL_CW


def preview_gl_options(
    blend_mode: str,
    material_state: MaterialPreviewState | None = None,
) -> dict:
    """Build pyqtgraph GL state for one preview submesh."""
    state = material_state or MaterialPreviewState()
    base_key = "translucent" if blend_mode in {"blend", "fade"} else "opaque"
    opts = dict(GLOptions[base_key])

    if blend_mode == "blend":
        opts["glDepthMask"] = (False,)
    elif blend_mode == "fade":
        # Uniform material-alpha decals (e.g. h_kage) still write depth so later
        # models and opaque surfaces are not tinted through solid geometry.
        opts["glDepthMask"] = (True,)
    elif blend_mode == "cutout":
        opts[GL.GL_ALPHA_TEST] = True
        opts["glAlphaFunc"] = (GL.GL_GREATER, float(state.alpha_cutoff))

    if state.double_sided:
        opts[GL.GL_CULL_FACE] = False
    else:
        opts[GL.GL_CULL_FACE] = True
        opts["glFrontFace"] = (_PREVIEW_FRONT_FACE,)

    return opts
