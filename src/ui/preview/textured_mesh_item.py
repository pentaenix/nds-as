"""GPU-textured mesh preview with nearest-neighbor sampling for pixel art."""
from __future__ import annotations

import numpy as np
from OpenGL import GL
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import QOpenGLBuffer
from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem
from pyqtgraph.opengl.shaders import FragmentShader, ShaderProgram, VertexShader

from ...platforms.nds.gltf.preview_textures import MaterialPreviewState
from .preview_gl import preview_gl_options

_PIXEL_TEXTURE_VS = """
uniform mat4 u_mvp;
attribute vec4 a_position;
attribute vec2 a_texcoord;
varying vec2 v_texcoord;
void main() {
    v_texcoord = a_texcoord;
    gl_Position = u_mvp * a_position;
}
"""

_PIXEL_TEXTURE_FS = """
#ifdef GL_ES
precision mediump float;
#endif
uniform sampler2D u_texture;
uniform float u_alpha;
uniform float u_alpha_cutoff;
uniform float u_alpha_mode;
varying vec2 v_texcoord;
void main() {
    vec4 texel = texture2D(u_texture, v_texcoord);
    texel.a *= u_alpha;
    if (u_alpha_mode > 0.5 && u_alpha_mode < 1.5 && texel.a < u_alpha_cutoff) {
        discard;
    }
    gl_FragColor = texel;
}
"""

_ALPHA_MODE_MAP = {"OPAQUE": 0.0, "MASK": 1.0, "BLEND": 2.0}


def _pixel_texture_shader() -> ShaderProgram:
    return ShaderProgram(
        "pixelTexture",
        [VertexShader(_PIXEL_TEXTURE_VS), FragmentShader(_PIXEL_TEXTURE_FS)],
        uniforms={
            "u_alpha": [1.0],
            "u_alpha_cutoff": [0.5],
            "u_alpha_mode": [0.0],
        },
    )


_PIXEL_SHADER = _pixel_texture_shader()
# Register so getShaderProgram can resolve the name if needed.
ShaderProgram.names["pixelTexture"] = _PIXEL_SHADER


class GLTexturedMeshItem(GLGraphicsItem):
    """Draw a mesh with a PNG using GL_NEAREST for crisp NDS pixel art."""

    def __init__(
        self,
        vertexes: np.ndarray,
        faces: np.ndarray,
        texcoords: np.ndarray,
        image_rgba: np.ndarray,
        *,
        material_state: MaterialPreviewState | None = None,
        blend_mode: str = "opaque",
        draw_edges: bool = False,
        parentItem=None,
    ) -> None:
        super().__init__(parentItem=parentItem)
        self._blend_mode = (
            blend_mode
            if blend_mode in {"opaque", "cutout", "blend", "shadow"}
            else "opaque"
        )
        state = material_state or MaterialPreviewState()
        self.setGLOptions(preview_gl_options(self._blend_mode, state))
        self._vertexes = np.ascontiguousarray(vertexes, dtype=np.float32)
        self._faces = np.ascontiguousarray(faces, dtype=np.uint32)
        self._texcoords = np.ascontiguousarray(texcoords, dtype=np.float32)
        self._image = np.ascontiguousarray(image_rgba, dtype=np.uint8)
        self._alpha = float(state.alpha)
        self._alpha_cutoff = float(state.alpha_cutoff)
        self._alpha_mode = _ALPHA_MODE_MAP.get(state.alpha_mode.upper(), 0.0)
        if self._blend_mode == "cutout" and self._alpha_mode < 0.5:
            self._alpha_mode = _ALPHA_MODE_MAP["MASK"]
        self._draw_edges = bool(draw_edges)
        self._tex_id: int | None = None
        self._buffers_uploaded = False

        self._vbo_position = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._vbo_texcoord = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        self._ibo_faces = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)

    def initializeGL(self) -> None:
        self._upload_texture()

    def _upload_texture(self) -> None:
        if self._tex_id is not None:
            return
        self._tex_id = int(GL.glGenTextures(1))
        height, width = self._image.shape[:2]
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D,
            0,
            GL.GL_RGBA,
            width,
            height,
            0,
            GL.GL_RGBA,
            GL.GL_UNSIGNED_BYTE,
            self._image,
        )
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def _upload_buffers(self) -> None:
        if self._buffers_uploaded:
            return

        def upload(vbo, arr: np.ndarray) -> None:
            if not vbo.isCreated():
                vbo.create()
            vbo.bind()
            vbo.allocate(arr, arr.nbytes)
            vbo.release()

        upload(self._vbo_position, self._vertexes)
        upload(self._vbo_texcoord, self._texcoords)
        upload(self._ibo_faces, self._faces.reshape(-1))
        self._buffers_uploaded = True

    def paint(self) -> None:
        self.setupGLState()
        if self._tex_id is None:
            self._upload_texture()
        self._upload_buffers()

        depth_mask_was = None
        if self._blend_mode == "blend":
            depth_mask_was = GL.glGetBooleanv(GL.GL_DEPTH_WRITEMASK)
            GL.glDepthMask(GL.GL_FALSE)

        mat_mvp = np.array(self.mvpMatrix().data(), dtype=np.float32)
        context = QOpenGLContext.currentContext()
        es2_compat = context.hasExtension(b"GL_ARB_ES2_compatibility") if context is not None else False
        shader = _PIXEL_SHADER
        program = shader.program(es2_compat=es2_compat)
        shader["u_alpha"] = [self._alpha]
        shader["u_alpha_cutoff"] = [self._alpha_cutoff]
        shader["u_alpha_mode"] = [self._alpha_mode]
        with shader:
            loc = GL.glGetUniformLocation(program, "u_mvp")
            if loc != -1:
                GL.glUniformMatrix4fv(loc, 1, False, mat_mvp)
            tex_loc = GL.glGetUniformLocation(program, "u_texture")
            if tex_loc != -1:
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
                GL.glUniform1i(tex_loc, 0)

            pos_loc = GL.glGetAttribLocation(program, "a_position")
            uv_loc = GL.glGetAttribLocation(program, "a_texcoord")
            enabled: list[int] = []

            if pos_loc != -1:
                self._vbo_position.bind()
                GL.glVertexAttribPointer(pos_loc, 3, GL.GL_FLOAT, False, 0, None)
                self._vbo_position.release()
                GL.glEnableVertexAttribArray(pos_loc)
                enabled.append(pos_loc)

            if uv_loc != -1:
                self._vbo_texcoord.bind()
                GL.glVertexAttribPointer(uv_loc, 2, GL.GL_FLOAT, False, 0, None)
                self._vbo_texcoord.release()
                GL.glEnableVertexAttribArray(uv_loc)
                enabled.append(uv_loc)

            self._ibo_faces.bind()
            GL.glDrawElements(GL.GL_TRIANGLES, self._faces.size, GL.GL_UNSIGNED_INT, None)
            self._ibo_faces.release()

            for loc in enabled:
                GL.glDisableVertexAttribArray(loc)

            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

        if depth_mask_was is not None:
            GL.glDepthMask(depth_mask_was)

    def dispose_gl(self) -> None:
        """Release GPU buffers/textures when the preview scene is swapped."""
        if self._tex_id is not None:
            try:
                GL.glDeleteTextures(1, [int(self._tex_id)])
            except Exception:
                pass
            self._tex_id = None
        for vbo in (self._vbo_position, self._vbo_texcoord, self._ibo_faces):
            if vbo.isCreated():
                vbo.destroy()
        self._buffers_uploaded = False
