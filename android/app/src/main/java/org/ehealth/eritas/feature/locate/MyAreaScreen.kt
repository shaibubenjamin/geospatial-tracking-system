package org.ehealth.eritas.feature.locate

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.launch
import org.ehealth.eritas.core.model.NearResponse
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import kotlin.math.roundToInt

/**
 * The core field-coverage aid. Reads the device's GPS and asks the server
 * which settlement/ward the user is standing in, how covered it is, and the
 * nearest settlement still left to cover - for the selected project.
 */
@Composable
fun MyAreaScreen(projectId: Int?) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var result by remember { mutableStateOf<NearResponse?>(null) }

    fun locate() {
        loading = true
        error = null
        scope.launch {
            try {
                val loc = getCurrentLocation(context)
                if (loc == null) {
                    error = "Could not get a GPS fix. Enable location and try again."
                    return@launch
                }
                result = ServiceLocator.api.near(loc.latitude, loc.longitude, projectId)
            } catch (e: Exception) {
                error = "Lookup failed: ${e.message ?: "network error"}"
            } finally {
                loading = false
            }
        }
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { granted -> if (granted) locate() else error = "Location permission is required." }

    fun requestLocate() {
        val granted = ContextCompat.checkSelfPermission(
            context, Manifest.permission.ACCESS_FINE_LOCATION
        ) == PackageManager.PERMISSION_GRANTED
        if (granted) locate() else permissionLauncher.launch(Manifest.permission.ACCESS_FINE_LOCATION)
    }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Where am I?", style = MaterialTheme.typography.headlineSmall)
        Text(
            "Check your current settlement's coverage and where to head next.",
            style = MaterialTheme.typography.bodyMedium,
        )
        Spacer(Modifier.height(16.dp))
        Button(onClick = { requestLocate() }, enabled = !loading) {
            if (loading) {
                CircularProgressIndicator(Modifier.height(20.dp))
            } else {
                Text("Use my location")
            }
        }
        Spacer(Modifier.height(16.dp))

        error?.let {
            Text(it, color = MaterialTheme.colorScheme.error)
            Spacer(Modifier.height(12.dp))
        }

        result?.let { r ->
            val cur = r.current
            if (cur == null) {
                Card(Modifier.fillMaxWidth()) {
                    Text(
                        "You don't appear to be inside any mapped settlement for this campaign.",
                        Modifier.padding(16.dp),
                    )
                }
            } else {
                Card(
                    Modifier.fillMaxWidth(),
                    colors = CardDefaults.cardColors(
                        containerColor = if (cur.isCovered) CoverageGood else CoverageLow
                    ),
                ) {
                    Column(Modifier.padding(16.dp)) {
                        Text(
                            "You are in",
                            style = MaterialTheme.typography.labelMedium,
                            color = Color.White,
                        )
                        Text(
                            cur.settlementName ?: "Unknown settlement",
                            style = MaterialTheme.typography.titleLarge,
                            color = Color.White,
                        )
                        Text(
                            "${cur.wardName ?: "-"} • ${cur.lgaName ?: "-"}",
                            color = Color.White,
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            if (cur.isCovered) {
                                "Covered - ${cur.completenessPct.roundToInt()}% complete"
                            } else {
                                "Not yet covered - ${cur.completenessPct.roundToInt()}% complete"
                            },
                            style = MaterialTheme.typography.titleMedium,
                            color = Color.White,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            if (r.recommendations.isNotEmpty()) {
                Column(Modifier.fillMaxWidth()) {
                    Text(
                        "Where to cover next",
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        "Nearest settlements still needing coverage - tap one for directions.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(8.dp))
                    r.recommendations.forEach { next ->
                        val canNavigate = next.lat != null && next.lon != null
                        Card(
                            Modifier
                                .fillMaxWidth()
                                .padding(bottom = 8.dp)
                                .then(
                                    if (canNavigate) Modifier.clickable {
                                        openDirections(context, next.lat!!, next.lon!!, next.settlementName)
                                    } else Modifier
                                ),
                        ) {
                            Row(
                                Modifier.fillMaxWidth().padding(14.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Column(Modifier.weight(1f)) {
                                    Text(
                                        next.settlementName ?: "Unknown",
                                        style = MaterialTheme.typography.titleSmall,
                                    )
                                    Text(
                                        "${next.wardName ?: "-"} • ${next.lgaName ?: "-"}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                    Text(
                                        formatDistance(next.distanceM) + " away" +
                                            if (canNavigate) " • tap for directions" else "",
                                        style = MaterialTheme.typography.bodySmall,
                                    )
                                }
                                Spacer(Modifier.height(0.dp))
                                Text(
                                    "${next.completenessPct.roundToInt()}%",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = covColor(next.completenessPct),
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Open the device's maps app pointed at the settlement so the team can route to it. */
private fun openDirections(context: android.content.Context, lat: Double, lon: Double, label: String?) {
    val q = "$lat,$lon" + (label?.let { "(${Uri.encode(it)})" } ?: "")
    val intent = Intent(Intent.ACTION_VIEW, Uri.parse("geo:$lat,$lon?q=$q"))
    runCatching { context.startActivity(intent) }
}

private fun covColor(pct: Double): Color = when {
    pct >= 70 -> CoverageGood
    pct >= 40 -> CoverageMid
    else -> CoverageLow
}

private fun formatDistance(meters: Double): String =
    if (meters >= 1000) "${(meters / 100).roundToInt() / 10.0} km" else "${meters.roundToInt()} m"
