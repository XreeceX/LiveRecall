package com.liverecall.glasses.service

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import com.liverecall.glasses.MainActivity
import com.liverecall.glasses.R

/**
 * Foreground service so the LiveKit publisher (mic + camera/glasses video)
 * survives backgrounding. We don't bind to it; SessionController in the
 * activity continues to own the Room. The service exists purely so Android
 * doesn't kill the publishing pipeline when the user leaves the screen.
 *
 * LiveKit's Android docs recommend a foreground service for any app that
 * needs to keep publishing while not in the foreground.
 */
class PublisherService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notif = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(
                NOTIF_ID, notif,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_CAMERA
            )
        } else {
            startForeground(NOTIF_ID, notif)
        }
        return START_STICKY
    }

    private fun buildNotification(): Notification {
        val tap = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(this, getString(R.string.ongoing_channel_id))
            .setContentTitle(getString(R.string.ongoing_notification_title))
            .setContentText(getString(R.string.ongoing_notification_text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(tap)
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        private const val NOTIF_ID = 0xCA11
        fun start(ctx: Context) =
            ctx.startForegroundService(Intent(ctx, PublisherService::class.java))
        fun stop(ctx: Context) =
            ctx.stopService(Intent(ctx, PublisherService::class.java))
    }
}
