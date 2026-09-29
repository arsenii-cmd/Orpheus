// Renders the app's Compose UI (../../app/src/main/java/dev/arco/orpheus/ui) on the desktop JVM,
// frame by frame, to PNG files and an MP4: a way to look at the motion without a phone or the Android SDK.
// Compose Multiplatform 1.5: the last one whose every artifact is on Maven Central (later ones pull
// androidx.* from Google Maven). The ui code sticks to APIs that exist both there and in the app's Compose.
plugins {
    kotlin("jvm") version "1.9.22"
    id("org.jetbrains.compose") version "1.5.12"
}

kotlin {
    sourceSets["main"].kotlin.srcDir("../../app/src/main/java/dev/arco/orpheus/ui")
}

dependencies {
    implementation(compose.desktop.currentOs)
    implementation(compose.material3)
}

tasks.register<JavaExec>("render") {
    description = "Render the preview frames: ./gradlew render (-Pout=dir)"
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("dev.arco.orpheus.preview.RenderKt")
    args(project.findProperty("out")?.toString() ?: layout.buildDirectory.dir("frames").get().asFile.path)
    workingDir = projectDir
}
