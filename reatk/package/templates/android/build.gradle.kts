plugins {
    id("com.android.application") version "8.5.2"
    id("org.jetbrains.kotlin.android") version "1.9.24"
}

android {
    namespace = "@@NAMESPACE@@"
    compileSdk = 34

    defaultConfig {
        applicationId = "@@BUNDLE_ID@@"
        minSdk = 26          // AAudio, used for sound output
        targetSdk = 34
        versionCode = 1
        versionName = "@@VERSION@@"
        ndk { abiFilters += listOf("arm64-v8a", "armeabi-v7a") }
    }

    // The generated project is unsigned on purpose: build it with your own
    // debug or release key.
    buildTypes {
        release { isMinifyEnabled = false }
    }

    externalNativeBuild {
        cmake { path = "src/main/cpp/CMakeLists.txt" }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    // Keep the ROM and the manifest byte-identical in the APK.
    androidResources { noCompress += listOf("nds", "gba", "json") }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.1")
}
