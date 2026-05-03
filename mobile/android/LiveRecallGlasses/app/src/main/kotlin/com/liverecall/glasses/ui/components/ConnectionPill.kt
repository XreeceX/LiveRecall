package com.liverecall.glasses.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.liverecall.glasses.livekit.SessionController
import com.liverecall.glasses.ui.theme.Tokens

/**
 * Status pill matching the .pill / .pill.live styling from
 * phone/glasses.html. Color follows the connection state.
 */
@Composable
fun ConnectionPill(status: SessionController.Status) {
    val (label, bg, fg) = when (status) {
        is SessionController.Status.Idle ->
            Triple("DISCONNECTED", Tokens.PanelBorder, Tokens.Muted)
        is SessionController.Status.Connecting ->
            Triple("CONNECTING…", Tokens.Warn.copy(alpha = 0.15f), Tokens.Warn)
        is SessionController.Status.Live ->
            Triple("LIVE · POV", Tokens.Accent.copy(alpha = 0.18f), Tokens.Accent)
        is SessionController.Status.Error ->
            Triple("ERROR", Tokens.Err.copy(alpha = 0.18f), Tokens.Err)
    }
    Text(
        text = label,
        color = fg,
        fontSize = 11.sp,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier
            .clip(CircleShape)
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 4.dp)
    )
}
