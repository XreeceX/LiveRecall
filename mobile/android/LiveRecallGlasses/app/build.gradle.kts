// app/build.gradle.kts
//
// LiveRecallGlasses Android app. Min SDK 29 (Android 10) lines up with the
// floor Meta documents for the Wearables Device Access Toolkit. Compose +
// LiveKit Android client SDK + (when wired) the Wearables SDK.

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.liverecall.glasses"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.liverecall.glasses"
        minSdk = 29
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"

        // The Meta AI app deep-links back to us during Wearables registration
        // through this scheme; AndroidManifest declares the matching intent
        // filter on MainActivity.
        // META_APP_ID / META_CLIENT_TOKEN flow into <meta-data> in the
        // manifest so the toolkit can read them at runtime. Replace before
        // shipping — see README.md.
        manifestPlaceholders["appLinkScheme"] = "liverecallglasses"
        manifestPlaceholders["META_APP_ID"] = "REPLACE_ME_META_APP_ID"
        manifestPlaceholders["META_CLIENT_TOKEN"] = "REPLACE_ME_CLIENT_TOKEN"

        // BuildConfig fields exposed so app code can show a warning before
        // the toolkit credentials have been replaced with real values.
        buildConfigField("String", "META_APP_ID", "\"REPLACE_ME_META_APP_ID\"")
        buildConfigField("String", "META_CLIENT_TOKEN", "\"REPLACE_ME_CLIENT_TOKEN\"")
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }

    sourceSets["main"].java.srcDirs("src/main/kotlin")
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.10.01"))
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    // OkHttp for the /token call.
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // LiveKit Android client SDK. v2.x exposes Room + LocalParticipant +
    // custom video capturer surfaces we need to pump glasses frames in.
    implementation("io.livekit:livekit-android:2.7.0")
    // SurfaceViewRenderer / VideoView helpers
    implementation("io.livekit:livekit-android-compose-components:1.4.0")

    // Meta Wearables Device Access Toolkit (developer preview). Uncomment
    // once you've added the maven repo + credentials in settings.gradle.kts.
    // implementation("com.meta.wearables:dat-android:0.3.0")
}
