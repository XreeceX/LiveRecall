package com.liverecall.glasses.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore("liverecall.glasses.config")

/**
 * Persisted user inputs — same three fields as phone/glasses.html, plus a
 * "prefer glasses source" toggle so the app can run end-to-end on the phone
 * camera before the Wearables SDK is wired up.
 */
class AppConfig(private val context: Context) {

    data class Snapshot(
        val backendUrl: String,
        val identity: String,
        val room: String,
        val preferGlasses: Boolean,
    ) {
        val sessionId: String
            get() = if (room.startsWith("liverecall-")) room.removePrefix("liverecall-") else room
    }

    val flow: Flow<Snapshot> = context.dataStore.data.map { prefs ->
        Snapshot(
            backendUrl   = prefs[KEY_BACKEND]   ?: "http://localhost:8000",
            identity     = prefs[KEY_IDENTITY]  ?: "kalle-glasses",
            room         = prefs[KEY_ROOM]      ?: "liverecall-demo",
            preferGlasses = prefs[KEY_GLASSES] ?: true,
        )
    }

    suspend fun update(
        backendUrl: String? = null,
        identity: String? = null,
        room: String? = null,
        preferGlasses: Boolean? = null,
    ) {
        context.dataStore.edit { prefs ->
            backendUrl?.let { prefs[KEY_BACKEND] = it }
            identity?.let { prefs[KEY_IDENTITY] = it }
            room?.let { prefs[KEY_ROOM] = it }
            preferGlasses?.let { prefs[KEY_GLASSES] = it }
        }
    }

    private companion object {
        val KEY_BACKEND  = stringPreferencesKey("backend_url")
        val KEY_IDENTITY = stringPreferencesKey("identity")
        val KEY_ROOM     = stringPreferencesKey("room")
        val KEY_GLASSES  = booleanPreferencesKey("prefer_glasses")
    }
}
