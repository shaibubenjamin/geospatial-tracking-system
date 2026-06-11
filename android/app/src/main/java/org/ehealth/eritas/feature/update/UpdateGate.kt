package org.ehealth.eritas.feature.update

import android.app.DownloadManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material3.Button
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
import androidx.compose.ui.platform.LocalContext
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
 * Trigger an in-app download of the latest APK straight from the update button
 * (it lands in the notification shade — tap to install), instead of bouncing
 * the user to the download web page. Falls back to the web page if
 * DownloadManager isn't available.
 */
fun downloadUpdate(context: Context, info: VersionInfo) {
    val name = "eritas-" + (if (info.latest > 0) info.latest.toString() else "latest") + ".apk"
    val url = BuildConfig.BASE_URL.trimEnd('/') + "/apk/eritas-latest.apk"
    try {
        val req = DownloadManager.Request(Uri.parse(url))
            .setTitle("ERITAS update")
            .setDescription("Downloading the latest version…")
            .setMimeType("application/vnd.android.package-archive")
            .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
            .setDestinationInExternalFilesDir(context, Environment.DIRECTORY_DOWNLOADS, name)
        (context.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(req)
        Toast.makeText(
            context,
            "Downloading update… open it from your notifications to install.",
            Toast.LENGTH_LONG,
        ).show()
    } catch (e: Exception) {
        openUpdateUrl(context, info)   // fallback: the browser download page
    }
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
                .padding(32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(
                Icons.Filled.SystemUpdate,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onPrimary,
                modifier = Modifier.padding(bottom = 24.dp),
            )
            Text(
                "Update required",
                style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.onPrimary,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.padding(8.dp))
            Text(
                "This version of ERITAS MDA Coverage is no longer supported. " +
                    "Please install the latest version (${info.latestName}) to continue.",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onPrimary,
                textAlign = TextAlign.Center,
            )
            Spacer(Modifier.padding(16.dp))
            Button(onClick = { downloadUpdate(context, info) }) {
                Text("Update now")
            }
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
            TextButton(onClick = { downloadUpdate(context, info) }) { Text("Update") }
            TextButton(onClick = onDismiss) { Text("Later") }
        }
    }
}
