package org.ehealth.eritas.feature.locate

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
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
import kotlin.math.roundToInt

/**
 * The core field-coverage aid. Reads the device's GPS and asks the server
 * which settlement/ward the user is standing in, how covered it is, and the
 * nearest settlement still left to cover — for the selected project.
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
                            "${cur.wardName ?: "—"} • ${cur.lgaName ?: "—"}",
                            color = Color.White,
                        )
                        Spacer(Modifier.height(8.dp))
                        Text(
                            if (cur.isCovered) {
                                "Covered — ${cur.completenessPct.roundToInt()}% complete"
                            } else {
                                "Not yet covered — ${cur.completenessPct.roundToInt()}% complete"
                            },
                            style = MaterialTheme.typography.titleMedium,
                            color = Color.White,
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))

            r.nearestUncovered?.let { next ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp)) {
                        Text("Nearest settlement left to cover", style = MaterialTheme.typography.labelMedium)
                        Text(
                            next.settlementName ?: "Unknown",
                            style = MaterialTheme.typography.titleMedium,
                        )
                        Text("${next.wardName ?: "—"} • ${next.lgaName ?: "—"}")
                        Spacer(Modifier.height(4.dp))
                        Text(
                            "${formatDistance(next.distanceM)} away • ${next.completenessPct.roundToInt()}% complete",
                            style = MaterialTheme.typography.bodyMedium,
                        )
                    }
                }
            }
        }
    }
}

private fun formatDistance(meters: Double): String =
    if (meters >= 1000) "${(meters / 100).roundToInt() / 10.0} km" else "${meters.roundToInt()} m"
