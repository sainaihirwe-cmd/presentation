import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

android {
    namespace = "com.slidegen.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.slidegen.app"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        ndk {
            // Python 3.13 runs on 64-bit devices; x86_64 is for the emulator
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    // Release key in android/keystore.properties (git-ignored; back it up: updates need the same key).
    // Without it the APK is signed with the debug key, which Play Protect tends to block.
    val keystoreFile = rootProject.file("keystore.properties")
    signingConfigs {
        if (keystoreFile.exists()) {
            create("release") {
                val props = Properties().apply { keystoreFile.inputStream().use { load(it) } }
                storeFile = rootProject.file(props.getProperty("storeFile"))
                storePassword = props.getProperty("storePassword")
                keyAlias = props.getProperty("keyAlias")
                keyPassword = props.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

// The phone app runs the same Python code as the website: copy it in before each build.
val webApp = layout.buildDirectory.dir("slidegen-python")
val syncWebApp by tasks.registering(Sync::class) {
    from(rootDir.parentFile) {
        include("app.py", "slidegen/**", "templates/**", "static/**")
        exclude("**/__pycache__/**")
    }
    into(webApp)
}
tasks.named("preBuild") { dependsOn(syncWebApp) }
tasks.matching { it.name.matches(Regex("merge.*PythonSources")) }.configureEach { dependsOn(syncWebApp) }

chaquopy {
    defaultConfig {
        version = "3.13"
        pip {
            install("-r", "requirements-android.txt")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir(webApp)
        }
    }
}
