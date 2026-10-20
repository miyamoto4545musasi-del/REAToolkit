package @@NAMESPACE@@

import android.content.Context
import android.opengl.GLES20
import android.opengl.GLSurfaceView
import org.json.JSONObject
import java.nio.ByteBuffer
import java.nio.ByteOrder
import javax.microedition.khronos.egl.EGLConfig
import javax.microedition.khronos.opengles.GL10

/**
 * Presents the core's frames.
 *
 * A DS core emits both screens in one buffer, stacked vertically. The manifest
 * says where each screen goes on the device, so this draws the frame twice -
 * once with the top half of the texture, once with the bottom - into those
 * rectangles.
 *
 * Until a core is loaded, [running] stays false and a moving test pattern is
 * drawn instead, which makes it obvious whether the problem is the renderer or
 * the core.
 */
class EmuView(context: Context, private val manifest: JSONObject) :
    GLSurfaceView(context) {

    private val renderer = FrameRenderer()
    private var running = false

    init {
        setEGLContextClientVersion(2)
        setRenderer(renderer)
        renderMode = RENDERMODE_CONTINUOUSLY
    }

    /** Draw core frames when true, the test pattern when false. */
    fun setRunning(active: Boolean) {
        running = active
    }

    private inner class FrameRenderer : Renderer {

        private var program = 0
        private var texture = 0
        private var frame: ByteBuffer = allocate(256, 384)
        private var frameWidth = 256
        private var frameHeight = 384
        private var tick = 0

        private fun allocate(width: Int, height: Int): ByteBuffer =
            ByteBuffer.allocateDirect(width * height * 4).order(ByteOrder.nativeOrder())

        override fun onSurfaceCreated(unused: GL10?, config: EGLConfig?) {
            GLES20.glClearColor(0f, 0f, 0f, 1f)
            program = buildProgram()
            texture = createTexture()
        }

        override fun onSurfaceChanged(unused: GL10?, width: Int, height: Int) {
            GLES20.glViewport(0, 0, width, height)
        }

        override fun onDrawFrame(unused: GL10?) {
            GLES20.glClear(GLES20.GL_COLOR_BUFFER_BIT)

            if (running) {
                val width = EmuCore.frameWidth()
                val height = EmuCore.frameHeight()
                if (width > 0 && height > 0 && (width != frameWidth || height != frameHeight)) {
                    frameWidth = width
                    frameHeight = height
                    frame = allocate(width, height)
                }
                frame.rewind()
                if (!EmuCore.runFrame(frame)) return
            } else {
                drawTestPattern()
            }

            upload()
            val key = if (this@EmuView.width > this@EmuView.height) "landscape" else "portrait"
            val screens = manifest
                .optJSONObject("controls")
                ?.optJSONObject(key)
                ?.optJSONObject("screens")

            // Without a layout, fall back to one screen filling the view.
            if (screens == null) {
                drawQuad(0f, 0f, 1f, 1f, 0f, 1f)
                return
            }
            drawScreen(screens, "top", 0f, 0.5f)
            drawScreen(screens, "bottom", 0.5f, 1f)
        }

        private fun drawScreen(screens: JSONObject, name: String, from: Float, to: Float) {
            val rect = screens.optJSONArray(name) ?: return
            drawQuad(
                rect.getDouble(0).toFloat(),
                rect.getDouble(1).toFloat(),
                rect.getDouble(2).toFloat(),
                rect.getDouble(3).toFloat(),
                from,
                to,
            )
        }

        /** Rectangle in view fractions, textured with a horizontal band. */
        private fun drawQuad(x: Float, y: Float, w: Float, h: Float, from: Float, to: Float) {
            val left = x * 2f - 1f
            val right = (x + w) * 2f - 1f
            val top = 1f - y * 2f
            val bottom = 1f - (y + h) * 2f

            val vertices = floatArrayOf(
                left, top, 0f, from,
                left, bottom, 0f, to,
                right, top, 1f, from,
                right, bottom, 1f, to,
            )
            val buffer = ByteBuffer
                .allocateDirect(vertices.size * 4)
                .order(ByteOrder.nativeOrder())
                .asFloatBuffer()
            buffer.put(vertices).position(0)

            GLES20.glUseProgram(program)
            val position = GLES20.glGetAttribLocation(program, "aPosition")
            val coords = GLES20.glGetAttribLocation(program, "aTexCoord")
            buffer.position(0)
            GLES20.glVertexAttribPointer(position, 2, GLES20.GL_FLOAT, false, 16, buffer)
            buffer.position(2)
            GLES20.glVertexAttribPointer(coords, 2, GLES20.GL_FLOAT, false, 16, buffer)
            GLES20.glEnableVertexAttribArray(position)
            GLES20.glEnableVertexAttribArray(coords)
            GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texture)
            GLES20.glUniform1i(GLES20.glGetUniformLocation(program, "uTexture"), 0)
            GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)
        }

        private fun upload() {
            frame.rewind()
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, texture)
            GLES20.glTexImage2D(
                GLES20.GL_TEXTURE_2D, 0, GLES20.GL_RGBA,
                frameWidth, frameHeight, 0,
                GLES20.GL_RGBA, GLES20.GL_UNSIGNED_BYTE, frame,
            )
        }

        private fun drawTestPattern() {
            tick += 1
            frame.rewind()
            for (y in 0 until frameHeight) {
                for (x in 0 until frameWidth) {
                    val checker = ((x + tick) / 16 + y / 16) % 2 == 0
                    val value = if (checker) 0x40 else 0xC0
                    frame.put(value.toByte())
                    frame.put((value / 2).toByte())
                    frame.put((0xFF - value).toByte())
                    frame.put(0xFF.toByte())
                }
            }
            frame.rewind()
        }

        private fun createTexture(): Int {
            val ids = IntArray(1)
            GLES20.glGenTextures(1, ids, 0)
            GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, ids[0])
            GLES20.glTexParameteri(
                GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_NEAREST
            )
            GLES20.glTexParameteri(
                GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_NEAREST
            )
            GLES20.glTexParameteri(
                GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE
            )
            GLES20.glTexParameteri(
                GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE
            )
            return ids[0]
        }

        private fun buildProgram(): Int {
            val vertex = """
                attribute vec2 aPosition;
                attribute vec2 aTexCoord;
                varying vec2 vTexCoord;
                void main() {
                    vTexCoord = aTexCoord;
                    gl_Position = vec4(aPosition, 0.0, 1.0);
                }
            """.trimIndent()
            val fragment = """
                precision mediump float;
                varying vec2 vTexCoord;
                uniform sampler2D uTexture;
                void main() {
                    gl_FragColor = texture2D(uTexture, vTexCoord);
                }
            """.trimIndent()

            val handle = GLES20.glCreateProgram()
            GLES20.glAttachShader(handle, compile(GLES20.GL_VERTEX_SHADER, vertex))
            GLES20.glAttachShader(handle, compile(GLES20.GL_FRAGMENT_SHADER, fragment))
            GLES20.glLinkProgram(handle)
            return handle
        }

        private fun compile(type: Int, source: String): Int {
            val shader = GLES20.glCreateShader(type)
            GLES20.glShaderSource(shader, source)
            GLES20.glCompileShader(shader)
            return shader
        }
    }
}
