"""Emit an Android (APK) project around a title."""

from __future__ import annotations

from pathlib import Path

from ..convert.images import scale_rgba, write_png
from ..plugins import PluginContext, PluginRegistry
from .common import (
    PackageOptions,
    banner_of,
    build_manifest,
    write_manifest,
    write_text,
)

GRADLE_SETTINGS = """pluginManagement {{
    repositories {{
        google()
        mavenCentral()
        gradlePluginPortal()
    }}
}}
dependencyResolutionManagement {{
    repositories {{
        google()
        mavenCentral()
    }}
}}
rootProject.name = "{project}"
include(":app")
"""

APP_GRADLE = """plugins {{
    id("com.android.application") version "8.5.2"
    id("org.jetbrains.kotlin.android") version "1.9.24"
}}

android {{
    namespace = "{namespace}"
    compileSdk = 34

    defaultConfig {{
        applicationId = "{bundle_id}"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "{version}"
        ndk {{ abiFilters += listOf("arm64-v8a", "armeabi-v7a") }}
    }}

    // The generated project is unsigned on purpose: build it with your own
    // debug or release key.
    buildTypes {{
        release {{ isMinifyEnabled = false }}
    }}

    externalNativeBuild {{
        cmake {{ path = "src/main/cpp/CMakeLists.txt" }}
    }}

    compileOptions {{
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }}
    kotlinOptions {{ jvmTarget = "17" }}
    // Keep the ROM and the manifest byte-identical in the APK.
    androidResources {{ noCompress += listOf("nds", "gba", "json") }}
}}

dependencies {{
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.1")
}}
"""

ANDROID_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher"
        android:hardwareAccelerated="true"
        android:allowBackup="true">
        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:screenOrientation="{orientation}"
            android:configChanges="orientation|screenSize|keyboardHidden">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

MAIN_ACTIVITY = '''package {namespace}

import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

/**
 * Host activity for the converted title.
 *
 * The emulation core is not part of this project. [EmuCore] loads whichever
 * libretro core you place in `jniLibs/<abi>/` and hands it the ROM named in
 * `assets/reatk/manifest.json`.
 */
class MainActivity : AppCompatActivity() {{

    private lateinit var manifest: JSONObject

    override fun onCreate(savedInstanceState: Bundle?) {{
        super.onCreate(savedInstanceState)
        manifest = JSONObject(assets.open("reatk/manifest.json").bufferedReader().readText())

        val status = TextView(this)
        setContentView(status)

        val rom = manifest.getJSONObject("rom")
        val romPath = if (rom.getBoolean("embedded")) {{
            EmuCore.stageAssetRom(this, rom.getString("path"))
        }} else {{
            // Nothing is embedded: let the player pick a ROM they own.
            EmuCore.pickRomFromStorage(this)
        }}

        status.text = buildString {{
            appendLine(manifest.getJSONObject("app").getString("name"))
            appendLine("core: " + manifest.getJSONObject("core").getString("name"))
            appendLine(if (romPath == null) "waiting for a ROM" else "rom: $romPath")
            appendLine()
            appendLine("Attach the surface and start EmuCore.run() here; see")
            appendLine("src/main/cpp/reatk_host.c for the core-side entry points.")
        }}
    }}
}}
'''

EMU_CORE = '''package {namespace}

import android.app.Activity
import android.content.Context
import java.io.File

/**
 * JNI surface over `reatk_host.c`.
 *
 * Deliberately thin: the native side owns the core handle, the frame buffer
 * and the audio ring, and this object only starts, stops and feeds input.
 */
object EmuCore {{

    init {{
        System.loadLibrary("reatk_host")
    }}

    /** Open the core at [corePath] and load [romPath]. Returns false on failure. */
    external fun open(corePath: String, romPath: String): Boolean

    /** Run one frame; returns the RGBA frame buffer or null when not running. */
    external fun runFrame(): ByteArray?

    /** Button bitmask using the libretro RETRO_DEVICE_ID_JOYPAD_* order. */
    external fun setButtons(mask: Int)

    /** Stylus position in bottom-screen pixels, or (-1, -1) to lift it. */
    external fun setPointer(x: Int, y: Int)

    external fun close()

    /** Copy an embedded ROM out of assets so the core can mmap a real file. */
    fun stageAssetRom(context: Context, assetPath: String): String {{
        val target = File(context.filesDir, assetPath.substringAfterLast('/'))
        if (!target.exists()) {{
            context.assets.open(assetPath).use {{ input ->
                target.outputStream().use {{ input.copyTo(it) }}
            }}
        }}
        return target.absolutePath
    }}

    /**
     * Hook for the storage picker. Returning null means the UI should prompt;
     * wire this to ActivityResultContracts.OpenDocument in your own flow.
     */
    fun pickRomFromStorage(activity: Activity): String? = null
}}
'''

HOST_C = r'''/*
 * reatk_host.c - minimal libretro host for the generated app.
 *
 * Scope: load a core shared object, hand it a ROM, pump frames, and expose the
 * latest frame as RGBA to the JVM. Video presentation, audio output and save
 * states are left to the app side; the hooks they need are marked TODO.
 *
 * No core is bundled. Place one (for example melonds_libretro_android.so) in
 * app/src/main/jniLibs/<abi>/ and pass its path to EmuCore.open().
 */
#include <dlfcn.h>
#include <jni.h>
#include <stdlib.h>
#include <string.h>

#include "libretro.h"

static struct {
    void *handle;
    void (*retro_init)(void);
    void (*retro_deinit)(void);
    void (*retro_run)(void);
    bool (*retro_load_game)(const struct retro_game_info *);
    void (*retro_unload_game)(void);
    void (*retro_set_environment)(retro_environment_t);
    void (*retro_set_video_refresh)(retro_video_refresh_t);
    void (*retro_set_audio_sample_batch)(retro_audio_sample_batch_t);
    void (*retro_set_input_poll)(retro_input_poll_t);
    void (*retro_set_input_state)(retro_input_state_t);

    unsigned pixel_format;
    uint16_t buttons;
    int pointer_x, pointer_y;

    uint8_t *frame_rgba;
    size_t frame_capacity;
    unsigned frame_width, frame_height;
} core;

static bool environment_cb(unsigned cmd, void *data)
{
    switch (cmd) {
    case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
        core.pixel_format = *(const unsigned *)data;
        return true;
    case RETRO_ENVIRONMENT_GET_CAN_DUPE:
        *(bool *)data = true;
        return true;
    default:
        /* TODO: variables, rumble, log interface, save directories. */
        return false;
    }
}

static void ensure_frame(unsigned width, unsigned height)
{
    size_t needed = (size_t)width * height * 4;
    if (needed > core.frame_capacity) {
        free(core.frame_rgba);
        core.frame_rgba = malloc(needed);
        core.frame_capacity = core.frame_rgba ? needed : 0;
    }
    core.frame_width = width;
    core.frame_height = height;
}

/* Cores emit XRGB8888 or RGB565; normalise both to RGBA for the JVM side. */
static void video_cb(const void *data, unsigned width, unsigned height, size_t pitch)
{
    if (!data) return;
    ensure_frame(width, height);
    if (!core.frame_rgba) return;

    for (unsigned y = 0; y < height; y++) {
        const uint8_t *src = (const uint8_t *)data + y * pitch;
        uint8_t *dst = core.frame_rgba + (size_t)y * width * 4;
        for (unsigned x = 0; x < width; x++) {
            if (core.pixel_format == RETRO_PIXEL_FORMAT_XRGB8888) {
                dst[0] = src[2]; dst[1] = src[1]; dst[2] = src[0]; dst[3] = 0xFF;
                src += 4;
            } else {
                uint16_t p = *(const uint16_t *)src;
                dst[0] = (uint8_t)(((p >> 11) & 0x1F) * 255 / 31);
                dst[1] = (uint8_t)(((p >> 5) & 0x3F) * 255 / 63);
                dst[2] = (uint8_t)((p & 0x1F) * 255 / 31);
                dst[3] = 0xFF;
                src += 2;
            }
            dst += 4;
        }
    }
}

static size_t audio_cb(const int16_t *data, size_t frames)
{
    (void)data;
    /* TODO: push into an AAudio stream. Dropping audio keeps timing honest. */
    return frames;
}

static void input_poll_cb(void) {}

static int16_t input_state_cb(unsigned port, unsigned device, unsigned index, unsigned id)
{
    (void)index;
    if (port != 0) return 0;
    if (device == RETRO_DEVICE_JOYPAD)
        return (core.buttons >> id) & 1;
    if (device == RETRO_DEVICE_POINTER) {
        if (core.pointer_x < 0) return 0;
        if (id == RETRO_DEVICE_ID_POINTER_X) return (int16_t)core.pointer_x;
        if (id == RETRO_DEVICE_ID_POINTER_Y) return (int16_t)core.pointer_y;
        if (id == RETRO_DEVICE_ID_POINTER_PRESSED) return 1;
    }
    return 0;
}

#define BIND(sym) \
    core.sym = dlsym(core.handle, #sym); \
    if (!core.sym) { dlclose(core.handle); core.handle = NULL; return JNI_FALSE; }

JNIEXPORT jboolean JNICALL
Java_PACKAGE_1PATH_EmuCore_open(JNIEnv *env, jobject self, jstring core_path, jstring rom_path)
{
    (void)self;
    const char *core_utf = (*env)->GetStringUTFChars(env, core_path, NULL);
    core.handle = dlopen(core_utf, RTLD_NOW);
    (*env)->ReleaseStringUTFChars(env, core_path, core_utf);
    if (!core.handle) return JNI_FALSE;

    BIND(retro_init) BIND(retro_deinit) BIND(retro_run)
    BIND(retro_load_game) BIND(retro_unload_game)
    BIND(retro_set_environment) BIND(retro_set_video_refresh)
    BIND(retro_set_audio_sample_batch) BIND(retro_set_input_poll)
    BIND(retro_set_input_state)

    core.pixel_format = RETRO_PIXEL_FORMAT_0RGB1555;
    core.pointer_x = core.pointer_y = -1;

    core.retro_set_environment(environment_cb);
    core.retro_init();
    core.retro_set_video_refresh(video_cb);
    core.retro_set_audio_sample_batch(audio_cb);
    core.retro_set_input_poll(input_poll_cb);
    core.retro_set_input_state(input_state_cb);

    const char *rom_utf = (*env)->GetStringUTFChars(env, rom_path, NULL);
    struct retro_game_info info = {0};
    info.path = rom_utf;
    bool ok = core.retro_load_game(&info);
    (*env)->ReleaseStringUTFChars(env, rom_path, rom_utf);
    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jbyteArray JNICALL
Java_PACKAGE_1PATH_EmuCore_runFrame(JNIEnv *env, jobject self)
{
    (void)self;
    if (!core.handle) return NULL;
    core.retro_run();
    if (!core.frame_rgba) return NULL;
    size_t size = (size_t)core.frame_width * core.frame_height * 4;
    jbyteArray out = (*env)->NewByteArray(env, (jsize)size);
    if (out) (*env)->SetByteArrayRegion(env, out, 0, (jsize)size, (const jbyte *)core.frame_rgba);
    return out;
}

JNIEXPORT void JNICALL
Java_PACKAGE_1PATH_EmuCore_setButtons(JNIEnv *env, jobject self, jint mask)
{
    (void)env; (void)self;
    core.buttons = (uint16_t)mask;
}

JNIEXPORT void JNICALL
Java_PACKAGE_1PATH_EmuCore_setPointer(JNIEnv *env, jobject self, jint x, jint y)
{
    (void)env; (void)self;
    core.pointer_x = x;
    core.pointer_y = y;
}

JNIEXPORT void JNICALL
Java_PACKAGE_1PATH_EmuCore_close(JNIEnv *env, jobject self)
{
    (void)env; (void)self;
    if (!core.handle) return;
    core.retro_unload_game();
    core.retro_deinit();
    dlclose(core.handle);
    memset(&core, 0, sizeof(core));
}
'''

CMAKE = """cmake_minimum_required(VERSION 3.22)
project(reatk_host C)

# libretro.h is not vendored here. Fetch it from the libretro-common repository
# (or your core's source tree) and drop it next to this file before building.
add_library(reatk_host SHARED reatk_host.c)
target_include_directories(reatk_host PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(reatk_host dl log android)
"""

PROJECT_README = """# {app_name} (Android)

Generated by REAToolkit {version_tool} from `{source}`.

## What is here

    app/src/main/assets/reatk/manifest.json   title metadata + control layout
    app/src/main/cpp/reatk_host.c             libretro host, JNI entry points
    app/src/main/java/{namespace_path}/        activity and the core wrapper
    app/src/main/res/mipmap-*/ic_launcher.png icon converted from the banner

## What you have to add

1. **`libretro.h`** into `app/src/main/cpp/` (from libretro-common).
2. **A core** built for Android into `app/src/main/jniLibs/<abi>/`, for example
   `{core}_libretro_android.so`. No core binary is generated or shipped by this
   toolkit.
3. **A signing key.** The project is unsigned; build it with your own.
{rom_note}

## Build

    ./gradlew assembleDebug
    adb install -r app/build/outputs/apk/debug/app-debug.apk

## Legal

See `LEGAL.md` in the toolkit. Short version: do this with homebrew you wrote
or a dump of a cartridge you own, keep the result on your own device, and do
not redistribute the APK if it contains a ROM or BIOS you do not hold the
rights to.
"""


def _gradle_name(app_name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in app_name).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "reatk-app"


def _namespace(bundle_id: str) -> str:
    parts = [p for p in bundle_id.split(".") if p]
    return ".".join(parts)


def emit_android(
    rom,
    options: PackageOptions,
    registry: PluginRegistry | None = None,
    ctx: PluginContext | None = None,
) -> Path:
    """Write a Gradle project for *rom* under ``options.out_dir``."""
    options.check_embedding()
    registry = registry or PluginRegistry()
    ctx = ctx or PluginContext(options=options.plugin_options)

    manifest = registry.run_manifest(build_manifest(rom, options), ctx)
    app_name = manifest["app"]["name"]
    bundle_id = manifest["app"]["bundle_id"]
    namespace = _namespace(bundle_id)

    root = Path(options.out_dir)
    app = root / "app"
    main = app / "src" / "main"
    java_dir = main / "java" / Path(*namespace.split("."))

    orientation = {
        "portrait": "portrait",
        "landscape": "landscape",
        "both": "fullSensor",
    }.get(options.orientation, "fullSensor")

    # Gradle wants a plain identifier here, not the display name.
    write_text(root / "settings.gradle.kts", GRADLE_SETTINGS.format(project=_gradle_name(app_name)))
    write_text(
        app / "build.gradle.kts",
        APP_GRADLE.format(namespace=namespace, bundle_id=bundle_id, version=options.version),
    )
    write_text(main / "AndroidManifest.xml", ANDROID_MANIFEST.format(orientation=orientation))
    write_text(java_dir / "MainActivity.kt", MAIN_ACTIVITY.format(namespace=namespace))
    write_text(java_dir / "EmuCore.kt", EMU_CORE.format(namespace=namespace))

    # JNI symbol names encode the package: a.b.C -> Java_a_b_C_method.
    jni_prefix = namespace.replace("_", "_1").replace(".", "_")
    write_text(main / "cpp" / "reatk_host.c", HOST_C.replace("PACKAGE_1PATH", jni_prefix))
    write_text(main / "cpp" / "CMakeLists.txt", CMAKE)

    write_text(
        main / "res" / "values" / "strings.xml",
        '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
        f"    <string name=\"app_name\">{_xml_escape(app_name.replace(chr(10), ' '))}</string>\n"
        "</resources>\n",
    )

    banner = banner_of(rom)
    if banner is not None:
        for density, factor in (("mdpi", 1), ("hdpi", 2), ("xhdpi", 3), ("xxhdpi", 4)):
            size = 32 * factor
            write_png(
                main / "res" / f"mipmap-{density}" / "ic_launcher.png",
                scale_rgba(banner.icon_rgba(), 32, 32, factor),
                size,
                size,
            )

    write_manifest(manifest, main / "assets" / "reatk" / "manifest.json")

    rom_note = (
        "4. Nothing else - the ROM is embedded at "
        "`app/src/main/assets/reatk/game.nds`.\n"
        if options.embed_rom
        else "4. **A ROM at runtime.** Nothing is embedded: the app asks the "
        "player to open a ROM from device storage.\n"
    )
    if options.embed_rom:
        target = main / "assets" / "reatk" / "game.nds"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(rom.data)

    from .. import __version__

    write_text(
        root / "README.md",
        PROJECT_README.format(
            app_name=app_name,
            version_tool=__version__,
            source=getattr(rom, "source", None) or "<memory>",
            namespace_path=namespace.replace(".", "/"),
            core=options.core,
            rom_note=rom_note,
        ),
    )

    registry.run_project(root, ctx)
    return root


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
