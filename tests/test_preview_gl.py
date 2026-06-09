from OpenGL import GL

from rae.glb_preview_textures import MaterialPreviewState
from rae.ui.preview.preview_gl import preview_gl_options


def test_preview_gl_options_enables_cull_for_single_sided_opaque():
    opts = preview_gl_options("opaque", MaterialPreviewState(double_sided=False))
    assert opts[GL.GL_CULL_FACE] is True
    assert opts["glFrontFace"] == (GL.GL_CW,)


def test_preview_gl_options_disables_cull_for_double_sided():
    opts = preview_gl_options("opaque", MaterialPreviewState(double_sided=True))
    assert opts[GL.GL_CULL_FACE] is False


def test_preview_gl_options_blend_disables_depth_write():
    opts = preview_gl_options("blend", MaterialPreviewState(alpha_mode="BLEND", alpha=0.3))
    assert opts["glDepthMask"] == (False,)
    assert opts[GL.GL_BLEND] is True


def test_preview_gl_options_shadow_writes_depth_without_culling():
    opts = preview_gl_options("shadow", MaterialPreviewState(alpha_mode="BLEND", alpha=0.29))
    assert opts["glDepthMask"] == (True,)
    assert opts[GL.GL_BLEND] is True
    assert opts[GL.GL_CULL_FACE] is False
