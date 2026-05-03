package com.liverecall.glasses

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import androidx.core.content.getSystemService

class GlassesApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        registerOngoingChannel()
    }

    private fun registerOngoingChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService<NotificationManager>() ?: return
        val ch = NotificationChannel(
            getString(R.string.ongoing_channel_id),
            getString(R.string.ongoing_channel_name),
            NotificationManager.IMPORTANCE_LOW
        )
        nm.createNotificationChannel(ch)
    }
}
