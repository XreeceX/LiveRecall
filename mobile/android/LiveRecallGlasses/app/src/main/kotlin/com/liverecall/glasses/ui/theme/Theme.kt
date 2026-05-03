package com.liverecall.glasses.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp

object Tokens {
    val Background    = Color(0xFF0A0D18)
    val Panel         = Color(0xFF131A2A)
    val PanelBorder   = Color(0xFF1F2937)
    val Text          = Color(0xFFE5E7EB)
    val Muted         = Color(0xFF94A3B8)
    val Accent        = Color(0xFFA78BFA)
    val Accent2       = Color(0xFF60A5FA)
    val Warn          = Color(0xFFFBBF24)
    val Err           = Color(0xFFF87171)

    val PanelShape = RoundedCornerShape(14.dp)
    val InputShape = RoundedCornerShape(10.dp)
    val ButtonShape = RoundedCornerShape(12.dp)
}

private val LiveRecallScheme = darkColorScheme(
    primary = Tokens.Accent,
    secondary = Tokens.Accent2,
    background = Tokens.Background,
    surface = Tokens.Panel,
    onPrimary = Tokens.Background,
    onSecondary = Tokens.Background,
    onBackground = Tokens.Text,
    onSurface = Tokens.Text,
    error = Tokens.Err,
)

@Composable
fun LiveRecallGlassesTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = LiveRecallScheme, content = content)
}
