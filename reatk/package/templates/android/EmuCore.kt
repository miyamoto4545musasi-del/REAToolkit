package @@NAMESPACE@@

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.nio.ByteBuffer

/**
 * JNI surface over `reatk_host.c`.
 *
 * Deliberately thin: the native side owns the core handle, the frame buffer
 * and the audio stream, and this object only starts, stops and feeds input.
 * Frames are written into a caller-owned direct [ByteBuffer] so a running game
 * allocates nothing per frame.
 */
object EmuCore {

    init {
        System.loadLibrary("reatk_host")
    }

    /** Button bit positions, in libretro's RETRO_DEVICE_ID_JOYPAD_* order. */
    object Button {
        const val B = 0
        const val Y = 1
        const val SELECT = 2
        const val START = 3
        const val UP = 4
        const val DOWN = 5
        const val LEFT = 6
        const val RIGHT = 7
        const val A = 8
        const val X = 9
        const val L = 10
        const val R = 11

        fun of(id: String): Int = when (id.lowercase()) {
            "a" -> A
            "b" -> B
            "x" -> X
            "y" -> Y
            "l" -> L
            "r" -> R
            "start" -> START
            "select" -> SELECT
            "up" -> UP
            "down" -> DOWN
            "left" -> LEFT
            "right" -> RIGHT
            else -> -1
        }
    }

    /** Where the core should look for BIOS/firmware and write saves. */
    external fun setSystemDirectory(path: String)

    external fun open(corePath: String, romPath: String): Boolean

    /** Run one frame into [into]; returns false when nothing was produced. */
    external fun runFrame(into: ByteBuffer): Boolean

    external fun frameWidth(): Int

    external fun frameHeight(): Int

    external fun setButtons(mask: Int)

    /** Stylus position in frame pixels, or (-1, -1) to lift it. */
    external fun setPointer(x: Int, y: Int)

    external fun close()

    fun coreFileName(manifest: JSONObject): String =
        "lib" + manifest.getJSONObject("core").getString("name") + "_libretro_android.so"

    /** The core shipped in this APK, if one was added to `jniLibs/`. */
    fun bundledCorePath(context: Context, manifest: JSONObject): String? {
        val candidate = File(context.applicationInfo.nativeLibraryDir, coreFileName(manifest))
        return if (candidate.isFile) candidate.absolutePath else null
    }

    /** Copy an embedded ROM out of assets so the core can open a real file. */
    fun stageAssetRom(context: Context, assetPath: String): String {
        val target = File(context.filesDir, assetPath.substringAfterLast('/'))
        if (!target.exists()) {
            context.assets.open(assetPath).use { input ->
                target.outputStream().use { input.copyTo(it) }
            }
        }
        return target.absolutePath
    }
}
