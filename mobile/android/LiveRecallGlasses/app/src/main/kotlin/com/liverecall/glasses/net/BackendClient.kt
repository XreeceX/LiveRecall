package com.liverecall.glasses.net

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * Same wire shape as phone/glasses.html → POST /token. Returns the
 * LiveKit URL + JWT plus the capture_mode the backend ended up persisting
 * on the session document.
 */
@Serializable
data class TokenResponse(
    val token: String,
    val url: String,
    val room: String,
    val capture_mode: String,
)

object BackendClient {

    private val json = Json { ignoreUnknownKeys = true }

    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS)
        .build()

    /** Throws on HTTP errors with the body included for easy debugging. */
    suspend fun requestToken(
        backendUrl: String,
        identity: String,
        room: String,
        captureMode: String = "glasses",
    ): TokenResponse = withContext(Dispatchers.IO) {
        val base = backendUrl.trim().trimEnd('/')
        require(base.startsWith("http://") || base.startsWith("https://")) {
            "Backend URL must be http(s)://"
        }
        val body = """
            {"identity":"${identity.escapeJson()}",
             "room":"${room.escapeJson()}",
             "capture_mode":"${captureMode.escapeJson()}"}
        """.trimIndent().toRequestBody("application/json".toMediaType())

        val req = Request.Builder().url("$base/token").post(body).build()
        http.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("backend HTTP ${resp.code}: $text")
            json.decodeFromString(TokenResponse.serializer(), text)
        }
    }

    private fun String.escapeJson(): String =
        replace("\\", "\\\\").replace("\"", "\\\"")
}
