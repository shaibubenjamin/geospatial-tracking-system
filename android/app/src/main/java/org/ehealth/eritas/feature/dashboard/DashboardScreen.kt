package org.ehealth.eritas.feature.dashboard

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.core.model.OverviewDto
import org.ehealth.eritas.core.net.ServiceLocator
import kotlin.math.roundToInt

private data class Stat(val label: String, val value: String)

@Composable
fun DashboardScreen(projectId: Int?) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var data by remember { mutableStateOf<OverviewDto?>(null) }

    LaunchedEffect(projectId) {
        loading = true
        error = null
        data = try {
            ServiceLocator.api.overview(projectId)
        } catch (e: Exception) {
            error = "Could not load overview: ${e.message ?: "network error"}"
            null
        }
        loading = false
    }

    when {
        loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
            Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        }
        data != null -> {
            val d = data!!
            val day = d.currentCampaignDay?.let { cur ->
                d.plannedDurationDays?.let { tot -> "$cur / $tot" } ?: cur.toString()
            } ?: "—"
            val stats = listOf(
                Stat("Coverage", "${d.coveragePct.roundToInt()}%"),
                Stat("Treated", formatCount(d.totalTreated)),
                Stat("Forms", formatCount(d.totalForms)),
                Stat("Teams active", d.teamsActive.toString()),
                Stat("LGAs covered", d.lgasCovered.toString()),
                Stat("Campaign day", day),
                Stat("QC flags", formatCount(d.totalQcFlags)),
                Stat("Error rate", "${d.errorRatePct.roundToInt()}%"),
                Stat("Refusals", formatCount(d.refusals)),
            )
            LazyVerticalGrid(
                columns = GridCells.Fixed(2),
                modifier = Modifier.fillMaxSize().padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                items(stats) { StatCard(it) }
            }
        }
    }
}

@Composable
private fun StatCard(stat: Stat) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp)) {
            Text(
                stat.value,
                style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(stat.label, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

private fun formatCount(n: Int): String = when {
    n >= 1_000_000 -> "${(n / 100_000) / 10.0}M"
    n >= 1_000 -> "${(n / 100) / 10.0}k"
    else -> n.toString()
}
