package com.liverecall.glasses.data

import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * Process-wide rolling log feed for the on-screen "Log" panel and Logcat.
 * Newest entries first; capped at 500 lines.
 */
object SessionLogger {

    enum class Level { INFO, OK, WARN, ERR }

    data class Entry(
        val timestampMs: Long,
        val level: Level,
        val message: String,
    )

    private const val TAG = "LiveRecallGlasses"
    private const val MAX_ENTRIES = 500

    private val _entries = MutableStateFlow<List<Entry>>(emptyList())
    val entries: StateFlow<List<Entry>> = _entries

    fun log(message: String, level: Level = Level.INFO) {
        val entry = Entry(System.currentTimeMillis(), level, message)
        _entries.update { current ->
            val next = ArrayList<Entry>(minOf(current.size + 1, MAX_ENTRIES))
            next.add(entry)
            for (e in current) {
                if (next.size >= MAX_ENTRIES) break
                next.add(e)
            }
            next
        }
        when (level) {
            Level.INFO, Level.OK -> Log.i(TAG, message)
            Level.WARN -> Log.w(TAG, message)
            Level.ERR -> Log.e(TAG, message)
        }
    }

    fun clear() { _entries.value = emptyList() }
}
