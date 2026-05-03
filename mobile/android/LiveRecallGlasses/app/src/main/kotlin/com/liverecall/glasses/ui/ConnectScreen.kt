package com.liverecall.glasses.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.liverecall.glasses.data.AppConfig
import com.liverecall.glasses.data.SessionLogger
import com.liverecall.glasses.livekit.SessionController
import com.liverecall.glasses.ui.components.ConnectionPill
import com.liverecall.glasses.ui.components.LogPanel
import com.liverecall.glasses.ui.components.PovPreview
import com.liverecall.glasses.ui.components.SettingsPanel
import com.liverecall.glasses.ui.theme.Tokens
import kotlinx.coroutines.flow.collectAsState
import kotlinx.coroutines.launch

/**
 * Single-screen UI mirroring phone/glasses.html: header pill row,
 * settings, POV preview, connect/leave row, log feed, footer.
 */
@Composable
fun ConnectScreen(
    config: AppConfig,
    session: SessionController,
) {
    val snapshot by config.flow.collectAsState(
        initial = AppConfig.Snapshot("http://localhost:8000", "kalle-glasses",
                                     "liverecall-demo", true)
    )
    val status by session.status.collectAsState()
    val logEntries by SessionLogger.entries.collectAsState()
    val track by session.publisher.localVideoTrack.collectAsState()
    val scope = rememberCoroutineScope()

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Tokens.Background),
    ) {
        Column(
            modifier = Modifier
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Header(status)

            SettingsPanel(
                snapshot = snapshot,
                enabled = status !is SessionController.Status.Live,
                onChange = { backend, identity, room, glasses ->
                    scope.launch {
                        config.update(
                            backendUrl = backend,
                            identity = identity,
                            room = room,
                            preferGlasses = glasses,
                        )
                    }
                },
            )

            PovPreview(track = track)

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(
                    onClick = { scope.launch { session.connect(snapshot) } },
                    enabled = status !is SessionController.Status.Live
                              && status !is SessionController.Status.Connecting,
                    shape = Tokens.ButtonShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color.Transparent,
                        contentColor = Tokens.Background,
                    ),
                    modifier = Modifier
                        .weight(1f)
                        .clip(Tokens.ButtonShape)
                        .background(
                            Brush.horizontalGradient(listOf(Tokens.Accent, Tokens.Accent2))
                        ),
                ) {
                    Text(
                        if (status is SessionController.Status.Live) "Live"
                        else "Connect (Ray-Ban POV)",
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Button(
                    onClick = { scope.launch { session.disconnect() } },
                    enabled = status is SessionController.Status.Live,
                    shape = Tokens.ButtonShape,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Tokens.Err.copy(alpha = 0.18f),
                        contentColor = Tokens.Err,
                    ),
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Leave", fontWeight = FontWeight.SemiBold)
                }
            }

            LogPanel(entries = logEntries, onClear = SessionLogger::clear)
            Footer()
        }
    }
}

@Composable
private fun Header(status: SessionController.Status) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text("LiveRecall", color = Tokens.Text, fontSize = 20.sp,
                 fontWeight = FontWeight.SemiBold)
            Text("· glasses (POV)", color = Tokens.Muted, fontSize = 12.sp)
        }
        Row(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            GlassesPill()
            ConnectionPill(status = status)
        }
    }
}

@Composable
private fun GlassesPill() {
    Text(
        "GLASSES · POV",
        color = Color(0xFFDDD6FE),
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier
            .clip(CircleShape)
            .background(
                Brush.horizontalGradient(
                    listOf(Tokens.Accent.copy(alpha = 0.25f),
                           Tokens.Accent2.copy(alpha = 0.18f))
                )
            )
            .border(1.dp, Tokens.Accent.copy(alpha = 0.45f), CircleShape)
            .padding(horizontal = 10.dp, vertical = 4.dp)
    )
}

@Composable
private fun Footer() {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tokens.PanelShape)
            .background(Tokens.Panel)
            .border(1.dp, Tokens.PanelBorder, Tokens.PanelShape)
            .padding(14.dp),
    ) {
        Text("Capture path", color = Tokens.Text, fontSize = 12.sp,
             fontWeight = FontWeight.SemiBold)
        Text(
            "Glasses → Bluetooth → this app (Wearables Device Access Toolkit) " +
            "→ Wi-Fi/LTE → LiveKit room liverecall-{session} → backend worker → " +
            "Vision/Router/etc. Backend already accepts capture_mode=glasses; " +
            "no server changes required.",
            color = Tokens.Muted,
            fontSize = 11.sp,
        )
    }
}
