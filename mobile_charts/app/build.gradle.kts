import java.util.Properties

plugins {
  alias(libs.plugins.android.application)
  alias(libs.plugins.compose.compiler)
  alias(libs.plugins.kotlin.serialization)
}

val keystorePropsFile = rootProject.file("keystore/keystore.properties")
val keystoreProps = Properties().apply {
  if (keystorePropsFile.exists()) keystorePropsFile.inputStream().use { load(it) }
}

val localPropsFile = rootProject.file("local.properties")
val localProps = Properties().apply {
  if (localPropsFile.exists()) localPropsFile.inputStream().use { load(it) }
}

val prodBaseUrl = localProps.getProperty("cortex.prodUrl")?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() }
  ?: "http://10.0.2.2:8003"
val homologBaseUrl = localProps.getProperty("cortex.homologUrl")?.trim()?.trimEnd('/')?.takeIf { it.isNotEmpty() }
  ?: "http://10.0.2.2:9003"

android {
  namespace = "com.cortexia.charts"
  compileSdk = 36

  defaultConfig {
    applicationId = "com.cortexia.charts"
    minSdk = 26
    targetSdk = 36
    versionCode = 1
    versionName = "1.0.0"
    buildConfigField("String", "PROD_BASE_URL", "\"$prodBaseUrl\"")
    buildConfigField("String", "HOMOLOG_BASE_URL", "\"$homologBaseUrl\"")
  }

  signingConfigs {
    if (keystorePropsFile.exists()) {
      create("release") {
        storeFile = rootProject.file("keystore/" + keystoreProps.getProperty("storeFile"))
        storePassword = keystoreProps.getProperty("storePassword")
        keyAlias = keystoreProps.getProperty("keyAlias")
        keyPassword = keystoreProps.getProperty("keyPassword")
      }
    }
  }

  buildTypes {
    release {
      isMinifyEnabled = false
      proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
      if (keystorePropsFile.exists()) {
        signingConfig = signingConfigs.getByName("release")
      }
    }
  }

  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
  }

  buildFeatures {
    compose = true
    aidl = false
    buildConfig = true
    shaders = false
  }

  packaging {
    resources {
      excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
  }
}

kotlin {
  jvmToolchain(17)
}

dependencies {
  val composeBom = platform(libs.androidx.compose.bom)
  implementation(composeBom)

  implementation(libs.androidx.core.ktx)
  implementation(libs.androidx.lifecycle.runtime.ktx)
  implementation(libs.androidx.activity.compose)
  implementation(libs.androidx.lifecycle.runtime.compose)
  implementation(libs.androidx.lifecycle.viewmodel.compose)

  implementation(libs.androidx.compose.ui)
  implementation(libs.androidx.compose.ui.tooling.preview)
  implementation(libs.androidx.compose.material3)
  implementation(libs.androidx.compose.material.icons)
  debugImplementation(libs.androidx.compose.ui.tooling)

  implementation(libs.kotlinx.coroutines.android)
  implementation(libs.kotlinx.serialization.json)
  implementation(libs.okhttp)

  testImplementation(libs.junit)
}
