package @@NAMESPACE@@

import android.net.Uri
import android.os.Bundle
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.File

/**
 * Host activity for the converted title.
 *
 * Reads `assets/reatk/manifest.json`, puts an [EmuView] on screen with the
 * control overlay the manifest describes, and starts the core. With no core
 * present the view shows a moving test pattern instead, so the render path can
 * be checked before a core is in place.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var manifest: JSONObject
    private lateinit var view: EmuView
    private lateinit var status: TextView

    private val pickRom =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
            if (uri != null) start(stageUri(uri))
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        manifest = JSONObject(assets.open("reatk/manifest.json").bufferedReader().readText())

        val layout = FrameLayout(this)
        view = EmuView(this, manifest)
        val controls = ControlOverlay(this, manifest)
        status = TextView(this).apply {
            setPadding(32, 32, 32, 32)
            setBackgroundColor(0xB0000000.toInt())
            setTextColor(0xFFFFFFFF.toInt())
        }
        layout.addView(view, ViewGroup.LayoutParams(-1, -1))
        layout.addView(controls, ViewGroup.LayoutParams(-1, -1))
        layout.addView(status, ViewGroup.LayoutParams(-1, -2))
        setContentView(layout)

        val rom = manifest.getJSONObject("rom")
        if (rom.getBoolean("embedded")) {
            start(EmuCore.stageAssetRom(this, rom.getString("path")))
        } else {
            status.text = "Open a ROM you own to start."
            pickRom.launch(arrayOf("*/*"))
        }
    }

    /** Copy a picked document into app storage: the core needs a real path. */
    private fun stageUri(uri: Uri): String {
        val target = File(cacheDir, "picked.nds")
        contentResolver.openInputStream(uri)?.use { input ->
            target.outputStream().use { input.copyTo(it) }
        }
        return target.absolutePath
    }

    private fun start(romPath: String) {
        // A DS core needs its BIOS and firmware here, and writes saves here.
        EmuCore.setSystemDirectory(filesDir.absolutePath)
        val corePath = EmuCore.bundledCorePath(this, manifest)
        val opened = corePath != null && EmuCore.open(corePath, romPath)
        status.text = when {
            opened -> ""
            corePath == null ->
                "No core found. Put ${EmuCore.coreFileName(manifest)} in " +
                    "jniLibs/<abi>/ and rebuild; showing a test pattern."
            else -> "The core refused this ROM; showing a test pattern."
        }
        status.visibility = if (status.text.isEmpty()) TextView.GONE else TextView.VISIBLE
        view.setRunning(opened)
    }

    override fun onPause() {
        super.onPause()
        view.onPause()
    }

    override fun onResume() {
        super.onResume()
        view.onResume()
    }

    override fun onDestroy() {
        super.onDestroy()
        EmuCore.close()
    }
}
