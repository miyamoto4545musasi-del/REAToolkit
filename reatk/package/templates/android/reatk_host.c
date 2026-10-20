/*
 * reatk_host.c - libretro host for the generated app.
 *
 * Owns the core handle, the frame buffer and the audio stream. Frames are
 * copied into a direct ByteBuffer the Kotlin side allocates once, so a running
 * game does not allocate per frame. Audio goes straight to AAudio.
 *
 * No core is bundled. Put one (for example @@CORE@@_libretro_android.so) in
 * app/src/main/jniLibs/<abi>/ and the app finds it at startup.
 */
#include <aaudio/AAudio.h>
#include <android/log.h>
#include <dlfcn.h>
#include <jni.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "libretro.h"

#define TAG "reatk"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, TAG, __VA_ARGS__)

static struct {
    void *handle;
    void (*retro_init)(void);
    void (*retro_deinit)(void);
    void (*retro_run)(void);
    bool (*retro_load_game)(const struct retro_game_info *);
    void (*retro_unload_game)(void);
    void (*retro_get_system_av_info)(struct retro_system_av_info *);
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

    AAudioStream *audio;
    char system_directory[1024];
} core;

/* -- libretro callbacks ------------------------------------------------- */

static bool environment_cb(unsigned cmd, void *data)
{
    switch (cmd) {
    case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
        core.pixel_format = *(const unsigned *)data;
        return true;
    case RETRO_ENVIRONMENT_GET_CAN_DUPE:
        *(bool *)data = true;
        return true;
    case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY:
    case RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY:
        /* A DS core looks here for BIOS and firmware files, and writes saves
         * here. Java sets it to the app's own storage before open(). */
        *(const char **)data = core.system_directory;
        return true;
    default:
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

/* Cores emit XRGB8888, RGB565 or 0RGB1555; normalise all three to RGBA. */
static void video_cb(const void *data, unsigned width, unsigned height, size_t pitch)
{
    if (!data) return;  /* a duped frame: keep what we have */
    ensure_frame(width, height);
    if (!core.frame_rgba) return;

    for (unsigned y = 0; y < height; y++) {
        const uint8_t *src = (const uint8_t *)data + y * pitch;
        uint8_t *dst = core.frame_rgba + (size_t)y * width * 4;
        for (unsigned x = 0; x < width; x++) {
            if (core.pixel_format == RETRO_PIXEL_FORMAT_XRGB8888) {
                dst[0] = src[2]; dst[1] = src[1]; dst[2] = src[0]; dst[3] = 0xFF;
                src += 4;
            } else if (core.pixel_format == RETRO_PIXEL_FORMAT_RGB565) {
                uint16_t p = *(const uint16_t *)src;
                dst[0] = (uint8_t)(((p >> 11) & 0x1F) * 255 / 31);
                dst[1] = (uint8_t)(((p >> 5) & 0x3F) * 255 / 63);
                dst[2] = (uint8_t)((p & 0x1F) * 255 / 31);
                dst[3] = 0xFF;
                src += 2;
            } else { /* 0RGB1555 */
                uint16_t p = *(const uint16_t *)src;
                dst[0] = (uint8_t)(((p >> 10) & 0x1F) * 255 / 31);
                dst[1] = (uint8_t)(((p >> 5) & 0x1F) * 255 / 31);
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
    if (!core.audio || !data) return frames;
    /* Non-blocking: a late frame is dropped rather than stalling emulation. */
    aaudio_result_t written = AAudioStream_write(core.audio, data, (int32_t)frames, 0);
    return written > 0 ? (size_t)written : frames;
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

/* -- audio -------------------------------------------------------------- */

static void open_audio(int32_t sample_rate)
{
    AAudioStreamBuilder *builder = NULL;
    if (AAudio_createStreamBuilder(&builder) != AAUDIO_OK) return;

    AAudioStreamBuilder_setFormat(builder, AAUDIO_FORMAT_PCM_I16);
    AAudioStreamBuilder_setChannelCount(builder, 2);
    AAudioStreamBuilder_setSampleRate(builder, sample_rate);
    AAudioStreamBuilder_setPerformanceMode(builder, AAUDIO_PERFORMANCE_MODE_LOW_LATENCY);

    if (AAudioStreamBuilder_openStream(builder, &core.audio) == AAUDIO_OK) {
        AAudioStream_requestStart(core.audio);
        LOGI("audio stream open at %d Hz", sample_rate);
    } else {
        LOGE("could not open an audio stream; running silent");
        core.audio = NULL;
    }
    AAudioStreamBuilder_delete(builder);
}

/* -- JNI ---------------------------------------------------------------- */

#define BIND(sym)                                      \
    core.sym = dlsym(core.handle, #sym);               \
    if (!core.sym) {                                   \
        LOGE("core is missing %s", #sym);              \
        dlclose(core.handle);                          \
        core.handle = NULL;                            \
        return JNI_FALSE;                              \
    }

JNIEXPORT void JNICALL
Java_@@JNI_PREFIX@@_EmuCore_setSystemDirectory(JNIEnv *env, jobject self, jstring path)
{
    (void)self;
    const char *utf = (*env)->GetStringUTFChars(env, path, NULL);
    snprintf(core.system_directory, sizeof(core.system_directory), "%s", utf);
    (*env)->ReleaseStringUTFChars(env, path, utf);
}

JNIEXPORT jboolean JNICALL
Java_@@JNI_PREFIX@@_EmuCore_open(JNIEnv *env, jobject self, jstring core_path, jstring rom_path)
{
    (void)self;
    const char *core_utf = (*env)->GetStringUTFChars(env, core_path, NULL);
    core.handle = dlopen(core_utf, RTLD_NOW);
    if (!core.handle) LOGE("dlopen failed: %s", dlerror());
    (*env)->ReleaseStringUTFChars(env, core_path, core_utf);
    if (!core.handle) return JNI_FALSE;

    BIND(retro_init) BIND(retro_deinit) BIND(retro_run)
    BIND(retro_load_game) BIND(retro_unload_game)
    BIND(retro_get_system_av_info)
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
    struct retro_game_info info;
    memset(&info, 0, sizeof(info));
    info.path = rom_utf;
    bool ok = core.retro_load_game(&info);
    (*env)->ReleaseStringUTFChars(env, rom_path, rom_utf);
    if (!ok) {
        LOGE("the core refused the ROM");
        return JNI_FALSE;
    }

    struct retro_system_av_info av_info;
    memset(&av_info, 0, sizeof(av_info));
    core.retro_get_system_av_info(&av_info);
    ensure_frame(av_info.geometry.base_width, av_info.geometry.base_height);
    open_audio((int32_t)av_info.timing.sample_rate);
    LOGI("core running: %ux%u at %.2f fps",
         av_info.geometry.base_width, av_info.geometry.base_height, av_info.timing.fps);
    return JNI_TRUE;
}

JNIEXPORT jboolean JNICALL
Java_@@JNI_PREFIX@@_EmuCore_runFrame(JNIEnv *env, jobject self, jobject buffer)
{
    (void)self;
    if (!core.handle) return JNI_FALSE;
    core.retro_run();
    if (!core.frame_rgba) return JNI_FALSE;

    uint8_t *destination = (*env)->GetDirectBufferAddress(env, buffer);
    jlong capacity = (*env)->GetDirectBufferCapacity(env, buffer);
    size_t size = (size_t)core.frame_width * core.frame_height * 4;
    if (!destination || capacity < (jlong)size) return JNI_FALSE;
    memcpy(destination, core.frame_rgba, size);
    return JNI_TRUE;
}

JNIEXPORT jint JNICALL
Java_@@JNI_PREFIX@@_EmuCore_frameWidth(JNIEnv *env, jobject self)
{
    (void)env; (void)self;
    return (jint)core.frame_width;
}

JNIEXPORT jint JNICALL
Java_@@JNI_PREFIX@@_EmuCore_frameHeight(JNIEnv *env, jobject self)
{
    (void)env; (void)self;
    return (jint)core.frame_height;
}

JNIEXPORT void JNICALL
Java_@@JNI_PREFIX@@_EmuCore_setButtons(JNIEnv *env, jobject self, jint mask)
{
    (void)env; (void)self;
    core.buttons = (uint16_t)mask;
}

JNIEXPORT void JNICALL
Java_@@JNI_PREFIX@@_EmuCore_setPointer(JNIEnv *env, jobject self, jint x, jint y)
{
    (void)env; (void)self;
    core.pointer_x = x;
    core.pointer_y = y;
}

JNIEXPORT void JNICALL
Java_@@JNI_PREFIX@@_EmuCore_close(JNIEnv *env, jobject self)
{
    (void)env; (void)self;
    if (core.audio) {
        AAudioStream_requestStop(core.audio);
        AAudioStream_close(core.audio);
    }
    if (core.handle) {
        core.retro_unload_game();
        core.retro_deinit();
        dlclose(core.handle);
    }
    free(core.frame_rgba);
    memset(&core, 0, sizeof(core));
}
