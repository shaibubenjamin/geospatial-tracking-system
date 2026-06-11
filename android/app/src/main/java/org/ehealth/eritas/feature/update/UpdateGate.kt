package org.ehealth.eritas.feature.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.auth.UpdateRequiredState
import org.ehealth.eritas.core.model.VersionInfo
import org.ehealth.eritas.core.net.ServiceLocator

sealed interface UpdateState {
    data object Loading : UpdateState
    data object Ok : UpdateState
    data class Optional(val info: VersionInfo) : UpdateState
    data class Required(val info: VersionInfo) : UpdateState
}

/** Resolve update_url (often a relative "/apk") against the configured host. */
fun openUpdateUrl(context: Context, info: VersionInfo) {
    val url = if (info.updateUrl.startsWith("http")) {
        info.updateUrl
    } else {
        BuildConfig.BASE_URL.trimEnd('/') + "/" + info.updateUrl.trimStart('/')
    }
    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
}

/**
 * Launch-time update check. Renders a full-screen blocking wall when the
 * install is below the server's minimum versionCode; otherwise renders the
 * app, passing an optional VersionInfo when a non-blocking update exists so
 * the host can show a dismissible banner.
 *
 * If /version is unreachable we do NOT block — the server's 426 gate is the
 * real enforcement, so an offline launch still lets the user in (and any data
 * call will be rejected server-side if the client is genuinely too old).
 */
@Composable
fun UpdateGate(content: @Composable (optionalUpdate: VersionInfo?) -> Unit) {
    var state by remember { mutableStateOf<UpdateState>(UpdateState.Loading) }

    LaunchedEffect(Unit) {
        state = try {
            val info = ServiceLocator.api.version()
            val vc = BuildConfig.VERSION_CODE
            when {
                info.min > 0 && vc < info.min -> UpdateState.Required(info)
                info.latest > vc -> UpdateState.Optional(info)
                else -> UpdateState.Ok
            }
        } catch (_: Exception) {
            UpdateState.Ok
        }
    }

    // A mid-session 426 (server rejected this build as too old) trips this —
    // flip to the blocking wall instead of letting a raw "HTTP 426" surface on
    // a screen. We fetch the latest /version for the wall's copy; if that fails,
    // fall back to a generic message pointing at the download URL.
    val forced = UpdateRequiredState.required
    LaunchedEffect(forced) {
        if (forced && state !is UpdateState.Required) {
            val info = runCatching { ServiceLocator.api.version() }.getOrNull()
                ?: VersionInfo(0, 0, "the latest version", "/apk")
            state = UpdateState.Required(info)
        }
    }

    when (val s = state) {
        UpdateState.Loading -> Box(
            Modifier.fillMaxSize(),
            contentAlignment = Alignment.Center,
        ) { CircularProgressIndicator() }
        is UpdateState.Required -> UpdateRequiredScreen(s.info)
        is UpdateState.Optional -> content(s.info)
        UpdateState.Ok -> content(null)
    }
}

@Composable
private fun UpdateRequiredScreen(info: VersionInfo) {
    val context = LocalContext.current
    Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.primary) {
        Column(
            Modifier
                .fillMaxSize()
                .padding(horizontal = 32.dp, vertical = 40.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(
                Icons.Filled.SystemUpdate,
                contentDescription = null,
                tint = Color.White,
                modifier = Modifier
                    .size(80.dp)
                    .padding(bottom = 24.dp),
            )
            Text(
                "Update required",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = Color.White,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(12.dp))
            Text(
                "You're on an older version of ERITAS. Install version " +
                    "${info.latestName} to continue using the app.",
                style = MaterialTheme.typography.bodyLarge,
                color = Color.White.copy(alpha = 0.92f),
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.height(36.dp))
            Button(
                onClick = { openUpdateUrl(context, info) },
                colors = ButtonDefaults.buttonColors(
                    containerColor = Color.White,
                    contentColor = MaterialTheme.colorScheme.primary,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(54.dp),
            ) {
                Icon(
                    Icons.Filled.SystemUpdate,
                    contentDescription = null,
                    modifier = Modifier
                        .size(20.dp)
                        .padding(end = 8.dp),
                )
                Text(
                    "Update now",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                )
            }
            Spacer(Modifier.height(14.dp))
            Text(
                "Opens the download page in your browser — tap the file when it " +
                    "finishes to install.",
                style = MaterialTheme.typography.bodySmall,
                color = Color.White.copy(alpha = 0.8f),
                textAlign = TextAlign.Center,
            )
        }
    }
}

/** Dismissible banner for an optional (non-blocking) update. */
@Composable
fun UpdateBanner(info: VersionInfo, onDismiss: () -> Unit) {
    val context = LocalContext.current
    Surface(
        Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.secondaryContainer,
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Version ${info.latestName} is available.",
                style = MaterialTheme.typography.bodyMedium,
                modifier = Modifier.padding(end = 8.dp),
            )
            Spacer(Modifier.weight(1f))
            TextButton(onClick = { openUpdateUrl(context, info) }) { Text("Update") }
            TextButton(onClick = onDismiss) { Text("Later") }
        }
    }
}
