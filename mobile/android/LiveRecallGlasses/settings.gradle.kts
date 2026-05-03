// settings.gradle.kts
//
// One-app Gradle build for the Ray-Ban Meta publisher.

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // Meta hosts the Wearables Device Access Toolkit Android SDK on
        // their developer portal. Once you've grabbed the credentials from
        // https://wearables.developer.meta.com/, uncomment and fill in:
        // maven {
        //     url = uri("https://wearables.developer.meta.com/.../maven")
        //     credentials {
        //         username = settings.providers.gradleProperty("META_USERNAME").get()
        //         password = settings.providers.gradleProperty("META_TOKEN").get()
        //     }
        // }
    }
}

rootProject.name = "LiveRecallGlasses"
include(":app")
