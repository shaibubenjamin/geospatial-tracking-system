import java.io.ByteArrayOutputStream
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

// ── Git-derived versioning ───────────────────────────────────────────────────
// versionCode  = number of commits on HEAD (monotonic; matches the server's
//                MIN_VERSION_CODE gate — see docs/apk-app-blueprint.md).
// versionName  = latest annotated tag (e.g. "0.1"), falling back to "0.1".
// Both degrade gracefully when git is unavailable (e.g. a source export) so
// the project still builds.
fun git(vararg args: String): String? = try {
    val out = ByteArrayOutputStream()
    val result = exec {
        commandLine(listOf("git") + args)
        standardOutput = out
        errorOutput = ByteArrayOutputStream()
        isIgnoreExitValue = true
    }
    if (result.exitValue == 0) out.toString().trim().ifBlank { null } else null
} catch (_: Exception) {
    null
}

val gitCommitCount: Int = git("rev-list", "--count", "HEAD")?.toIntOrNull() ?: 1
val gitVersionName: String =
    (git("describe", "--tags", "--abbrev=0") ?: "0.1").removePrefix("v")

// ── Release signing ──────────────────────────────────────────────────────────
// CI injects the keystore via a keystore.properties file (or env vars) it
// writes from GitHub secrets. Locally, if no keystore.properties exists the
// release build falls back to the debug signing config so it still assembles.
val keystorePropsFile = rootProject.file("keystore.properties")
val hasReleaseKeystore = keystorePropsFile.exists()
val keystoreProps = Properties().apply {
    if (hasReleaseKeystore) keystorePropsFile.inputStream().use { load(it) }
}

android {
    namespace = "org.ehealth.eritas"
    compileSdk = 34

    defaultConfig {
        applicationId = "org.ehealth.eritas"
        minSdk = 24
        targetSdk = 34
        versionCode = gitCommitCount
        versionName = gitVersionName

        // The backend the app talks to. Override per build type below.
        buildConfigField(
            "String",
            "BASE_URL",
            "\"https://eha-mda-dashboard.ehealthnigeria.org\"",
        )

        // Ship only real-device CPU ABIs. MapLibre's native .so libs are the
        // bulk of the APK and R8 can't shrink them; dropping x86/x86_64
        // (emulator-only) roughly halves the native payload. For an even
        // smaller download, switch to per-ABI splits / an app bundle.
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    signingConfigs {
        if (hasReleaseKeystore) {
            create("release") {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        debug {
            // Local dev against the Android emulator host (host machine's
            // localhost is 10.0.2.2 inside the emulator). Cleartext for this
            // host is whitelisted in network_security_config.xml.
            buildConfigField("String", "BASE_URL", "\"http://10.0.2.2:8090\"")
        }
        release {
            // R8 disabled: the APK no longer bundles native map libs, so the
            // shrink savings are small and not worth the obfuscation risk.
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = if (hasReleaseKeystore) {
                signingConfigs.getByName("release")
            } else {
                // Pilot fallback so `assembleRelease` works without secrets;
                // CI always provides the real keystore.
                signingConfigs.getByName("debug")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.navigation:navigation-compose:2.8.0")

    // Networking
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.retrofit2:converter-moshi:2.11.0")
    implementation("com.squareup.moshi:moshi:1.15.1")
    implementation("com.squareup.moshi:moshi-kotlin:1.15.1")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")

    // Secure token storage
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // Map: rendered with MapLibre GL JS inside a WebView (same engine as the
    // web dashboard). No native map SDK — avoids the on-device native renderer
    // crash and keeps the APK small (no bundled .so libraries).

    debugImplementation("androidx.compose.ui:ui-tooling")
}
