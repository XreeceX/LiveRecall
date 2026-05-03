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
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.liverecall.glasses.data.AppConfig
import com.liverecall.glasses.ui.theme.Tokens

@Composable
fun SettingsPanel(
    snapshot: AppConfig.Snapshot,
    enabled: Boolean,
    onChange: (
        backendUrl: String?,
        identity: String?,
        room: String?,
        preferGlasses: Boolean?,
    ) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(Tokens.PanelShape)
            .background(Tokens.Panel)
            .border(1.dp, Tokens.PanelBorder, Tokens.PanelShape)
            .padding(14.dp)
            .alpha(if (enabled) 1f else 0.6f),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Field(
            label = "Backend URL",
            value = snapshot.backendUrl,
            placeholder = "http://192.168.1.100:8000",
            keyboardType = KeyboardType.Uri,
            enabled = enabled,
            onChange = { onChange(it, null, null, null) },
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Field(
                label = "Identity",
                value = snapshot.identity,
                placeholder = "kalle-glasses",
                modifier = Modifier.weight(1f),
                enabled = enabled,
                onChange = { onChange(null, it, null, null) },
            )
            Field(
                label = "Room",
                value = snapshot.room,
                placeholder = "liverecall-demo",
                modifier = Modifier.weight(1f),
                enabled = enabled,
                onChange = { onChange(null, null, it, null) },
            )
        }
        Spacer(Modifier.height(2.dp))
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    "Use Ray-Ban Meta as source",
                    color = Tokens.Text,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Medium,
                )
                Text(
                    if (snapshot.preferGlasses)
                        "Toolkit + paired glasses (or Mock Device Kit)"
                    else "Phone camera (fallback while toolkit is unwired)",
                    color = Tokens.Muted,
                    fontSize = 11.sp,
                )
            }
            Switch(
                checked = snapshot.preferGlasses,
                onCheckedChange = { onChange(null, null, null, it) },
                enabled = enabled,
                colors = SwitchDefaults.colors(checkedThumbColor = Tokens.Accent),
            )
        }
    }
}

@Composable
private fun Field(
    label: String,
    value: String,
    placeholder: String,
    modifier: Modifier = Modifier,
    keyboardType: KeyboardType = KeyboardType.Text,
    enabled: Boolean = true,
    onChange: (String) -> Unit,
) {
    Column(modifier) {
        Text(label, color = Tokens.Muted, fontSize = 12.sp)
        Spacer(Modifier.height(4.dp))
        TextField(
            value = value,
            onValueChange = onChange,
            singleLine = true,
            placeholder = { Text(placeholder, color = Tokens.Muted) },
            enabled = enabled,
            keyboardOptions = KeyboardOptions(keyboardType = keyboardType),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = Tokens.Background,
                unfocusedContainerColor = Tokens.Background,
                disabledContainerColor = Tokens.Background,
                focusedTextColor = Tokens.Text,
                unfocusedTextColor = Tokens.Text,
                cursorColor = Tokens.Accent,
                focusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent,
                unfocusedIndicatorColor = androidx.compose.ui.graphics.Color.Transparent,
            ),
            modifier = Modifier
                .fillMaxWidth()
                .clip(Tokens.InputShape)
                .border(1.dp, Tokens.PanelBorder, Tokens.InputShape),
        )
    }
}
