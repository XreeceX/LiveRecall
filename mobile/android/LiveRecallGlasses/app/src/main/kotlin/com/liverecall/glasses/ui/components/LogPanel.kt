package com.liverecall.glasses.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.liverecall.glasses.data.SessionLogger
import com.liverecall.glasses.ui.theme.Tokens
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

@Composable
fun LogPanel(entries: List<SessionLogger.Entry>, onClear: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tokens.PanelShape)
            .background(Tokens.Panel)
            .border(1.dp, Tokens.PanelBorder, Tokens.PanelShape)
            .padding(14.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Log", color = Tokens.Muted, fontSize = 12.sp)
            Spacer(Modifier.height(0.dp).fillMaxWidth().padding(0.dp))
            TextButton(onClick = onClear) {
                Text("clear", color = Tokens.Muted, fontSize = 11.sp,
                     fontWeight = FontWeight.SemiBold)
            }
        }
        LazyColumn(
            modifier = Modifier
                .fillMaxWidth()
                .height(160.dp)
                .clip(Tokens.PanelShape)
                .background(Color(0xFF060914))
                .padding(10.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            items(entries) { e ->
                Text(
                    text = formatLine(e),
                    color = colorFor(e.level),
                    fontSize = 12.sp,
                    fontFamily = FontFamily.Monospace,
                )
            }
        }
    }
}

private val timeFormatter = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

private fun formatLine(e: SessionLogger.Entry): String =
    "[${timeFormatter.format(Date(e.timestampMs))}] ${e.message}"

private fun colorFor(level: SessionLogger.Level): Color = when (level) {
    SessionLogger.Level.INFO -> Tokens.Muted
    SessionLogger.Level.OK -> Tokens.Accent
    SessionLogger.Level.WARN -> Tokens.Warn
    SessionLogger.Level.ERR -> Tokens.Err
}
