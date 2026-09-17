plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val canonicalVersionSource = rootProject.file("../server/omp_remote/version.py").readText()
val canonicalVersionCode = Regex("""VERSION_CODE\s*=\s*(\d+)""")
    .find(canonicalVersionSource)
    ?.groupValues
    ?.get(1)
    ?.toInt()
    ?: error("VERSION_CODE is missing from server/omp_remote/version.py")
val canonicalVersionName = Regex("""VERSION_NAME\s*=\s*"([^"]+)"""")
    .find(canonicalVersionSource)
    ?.groupValues
    ?.get(1)
    ?: error("VERSION_NAME is missing from server/omp_remote/version.py")

android {
    namespace = "com.ompremote"
    compileSdk = 35

    buildFeatures {
        buildConfig = true
    }

    defaultConfig {
        applicationId = "com.ompremote"
        minSdk = 26
        targetSdk = 35
        versionCode = canonicalVersionCode
        versionName = canonicalVersionName
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }


    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}
tasks.register("copyVersionedDebugApk") {
    val versionedApk = layout.buildDirectory.file("outputs/apk/debug/omp-remote-v$canonicalVersionName.apk")
    outputs.file(versionedApk)
    doLast {
        val source = layout.buildDirectory.file("outputs/apk/debug/app-debug.apk").get().asFile
        val destination = versionedApk.get().asFile
        source.copyTo(destination, overwrite = true)
    }
}

tasks.configureEach {
    if (name == "assembleDebug") {
        finalizedBy("copyVersionedDebugApk")
    }
}

kotlin {
    jvmToolchain(21)
}
