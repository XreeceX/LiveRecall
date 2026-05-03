package com.liverecall.glasses.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.liverecall.glasses.ui.theme.Tokens
import io.livekit.android.compose.VideoTrackView
import io.livekit.android.room.track.LocalVideoTrack

/**
 * 16:9 viewport with the same purple accent treatment as the .pov-frame
 * block in phone/glasses.html. Renders the LiveKit local video track when
 * we have one (glasses stream or phone fallback both look identical from
 * here — same VideoTrackView).
 *
 * NOTE on the renderer: livekit-android-compose-components ships
 * `VideoTrackView` taking a `Track`. If your version pins a different
 * composable name (older builds export `VideoRenderer`), swap the call
 * below — the rest of the file is renderer-agnostic.
 */
@Composable
fun PovPreview(track: LocalVideoTrack?) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .aspectRatio(16f / 9f)
            .clip(RoundedCornerShape(16.dp))
            .background(Tokens.Background)
            .border(1.dp, Tokens.Accent.copy(alpha = 0.30f), RoundedCornerShape(16.dp)),
    ) {
        if (track != null) {
            VideoTrackView(
                videoTrack = track,
                modifier = Modifier.fillMaxSize(),
            )
        } else {
            Column(
                modifier = Modifier.fillMaxSize(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.Center,
            ) {
                Text("waiting for video", color = Tokens.Muted, fontSize = 13.sp,
                     fontWeight = FontWeight.Medium)
                Text("connect to start streaming", color = Tokens.Muted.copy(alpha = 0.7f),
                     fontSize = 11.sp)
            }
        }
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(8.dp)
                .border(1.dp, Tokens.Accent.copy(alpha = 0.25f), RoundedCornerShape(12.dp)),
        )
        Text(
            "POV · FIRST-PERSON",
            color = androidx.compose.ui.graphics.Color(0xFFDDD6FE),
            fontSize = 10.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier
                .padding(10.dp)
                .clip(RoundedCornerShape(6.dp))
                .background(Tokens.Background.copy(alpha = 0.6f))
                .border(1.dp, Tokens.Accent.copy(alpha = 0.35f), RoundedCornerShape(6.dp))
                .padding(horizontal = 8.dp, vertical = 4.dp),
        )
    }
}
