package com.liverecall.glasses

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.liverecall.glasses.data.AppConfig
import com.liverecall.glasses.data.SessionLogger
import com.liverecall.glasses.livekit.SessionController
import com.liverecall.glasses.service.PublisherService
import com.liverecall.glasses.ui.ConnectScreen
import com.liverecall.glasses.ui.theme.LiveRecallGlassesTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private lateinit var config: AppConfig
    private lateinit var session: SessionController

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { results ->
        results.forEach { (perm, granted) ->
            SessionLogger.log("perm: $perm = ${if (granted) "granted" else "denied"}")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = AppConfig(applicationContext)
        session = SessionController(applicationContext)

        ensureRuntimePermissions()
        PublisherService.start(applicationContext)

        setContent {
            LiveRecallGlassesTheme {
                ConnectScreen(config = config, session = session)
            }
        }

        // Wearables registration callback from the Meta AI app arrives here
        // as the activity's launch intent (URL scheme: liverecallglasses).
        // The toolkit's real handoff goes inside GlassesController once the
        // SDK dependency is enabled — we just log + ignore until then.
        intent?.data?.let { uri ->
            lifecycleScope.launch {
                SessionLogger.log("wearables: deep-link $uri", SessionLogger.Level.INFO)
            }
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        PublisherService.stop(applicationContext)
    }

    private fun ensureRuntimePermissions() {
        val needed = mutableListOf<String>()
        fun add(perm: String) {
            if (ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED) {
                needed += perm
            }
        }
        add(Manifest.permission.RECORD_AUDIO)
        add(Manifest.permission.CAMERA)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_CONNECT)
            add(Manifest.permission.BLUETOOTH_SCAN)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (needed.isNotEmpty()) permissionLauncher.launch(needed.toTypedArray())
    }
}
