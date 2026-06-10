package org.ehealth.eritas.feature.coverage

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.core.model.LgaCoverage
import org.ehealth.eritas.core.model.SettlementCoverage
import org.ehealth.eritas.core.model.WardCoverage
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import kotlin.math.roundToInt

private fun coverageColor(pct: Double) = when {
    pct >= 70 -> CoverageGood
    pct >= 40 -> CoverageMid
    else -> CoverageLow
}

@Composable
fun LgaCoverageScreen(projectId: Int?, onOpenMap: (String) -> Unit = {}) {
    var selectedLga by remember { mutableStateOf<String?>(null) }
    var selectedWard by remember { mutableStateOf<String?>(null) }

    when {
        // Level 3: settlements within the selected ward.
        selectedLga != null && selectedWard != null -> {
            BackHandler { selectedWard = null }
            SettlementCoveragePage(
                lga = selectedLga!!,
                ward = selectedWard!!,
                projectId = projectId,
                onBack = { selectedWard = null },
            )
        }
        // Level 2: wards within the selected LGA (tap a ward → its settlements).
        selectedLga != null -> {
            BackHandler { selectedLga = null }
            WardCoveragePage(
                lga = selectedLga!!,
                projectId = projectId,
                onBack = { selectedLga = null },
                onOpenMap = onOpenMap,
                onOpenWard = { selectedWard = it },
            )
        }
        // Level 1: LGAs.
        else -> LgaListPage(
            projectId = projectId,
            onOpenLga = { selectedLga = it },
            onOpenMap = onOpenMap,
        )
    }
}

@Composable
private fun LgaListPage(
    projectId: Int?,
    onOpenLga: (String?) -> Unit,
    onOpenMap: (String) -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var rows by remember { mutableStateOf<List<LgaCoverage>>(emptyList()) }

    LaunchedEffect(projectId) {
        loading = true
        error = null
        try {
            rows = ServiceLocator.api.coverageLga(projectId)
        } catch (e: Exception) {
            error = "Could not load LGA coverage: ${e.message ?: "network error"}"
        }
        loading = false
    }

    when {
        loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
            Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        }
        rows.isEmpty() -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
            Text("No LGA coverage data for this campaign yet.", textAlign = TextAlign.Center)
        }
        else -> LazyColumn(
            Modifier.fillMaxSize().padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item {
                Text(
                    "Coverage by LGA · tap to view wards",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(bottom = 4.dp),
                )
            }
            items(rows) { row ->
                LgaRow(row, onOpenMap = { row.lga?.let(onOpenMap) }) { onOpenLga(row.lga) }
            }
        }
    }
}

@Composable
private fun WardCoveragePage(
    lga: String,
    projectId: Int?,
    onBack: () -> Unit,
    onOpenMap: (String) -> Unit,
    onOpenWard: (String) -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var wards by remember { mutableStateOf<List<WardCoverage>>(emptyList()) }

    LaunchedEffect(lga) {
        loading = true
        error = null
        try {
            wards = ServiceLocator.api.coverageWard(lga, projectId)
        } catch (e: Exception) {
            error = "Could not load wards: ${e.message ?: "network error"}"
        }
        loading = false
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back to LGAs")
            }
            Text(
                "$lga · wards",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            // View this LGA on the map (ward-level focus isn't supported by the
            // map, so the pin zooms to the parent LGA).
            IconButton(onClick = { onOpenMap(lga) }) {
                Icon(Icons.Filled.Place, contentDescription = "View on map", tint = MaterialTheme.colorScheme.primary)
            }
        }
        when {
            loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
                Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
            }
            wards.isEmpty() -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
                Text("No ward coverage for this LGA yet.", textAlign = TextAlign.Center)
            }
            else -> LazyColumn(
                Modifier.fillMaxSize().padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                items(wards) { w -> WardCard(w) { w.wardName?.let(onOpenWard) } }
            }
        }
    }
}

@Composable
private fun LgaRow(row: LgaCoverage, onOpenMap: (String) -> Unit, onClick: () -> Unit) {
    val pct = row.coveragePct
    val color = coverageColor(pct)
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(start = 14.dp, end = 4.dp, top = 14.dp, bottom = 14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    row.lga ?: "Unknown",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "${pct.roundToInt()}%",
                        style = MaterialTheme.typography.titleMedium,
                        color = color,
                        fontWeight = FontWeight.Bold,
                    )
                    // Tapping the pin opens the Map tab on this LGA (doesn't
                    // trigger the card's drill-to-wards — the button consumes it).
                    IconButton(onClick = { row.lga?.let(onOpenMap) }) {
                        Icon(
                            Icons.Filled.Place,
                            contentDescription = "View on map",
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                    Icon(
                        Icons.AutoMirrored.Filled.KeyboardArrowRight,
                        contentDescription = "View wards",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            LinearProgressIndicator(
                progress = { (pct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(8.dp).padding(top = 6.dp),
                color = color,
            )
            Text(
                "${formatCount(row.actualTreated)} treated / ${formatCount(row.baselineTotal)} target" +
                    "  ·  ${row.teams} team(s)  ·  ${formatCount(row.forms)} forms",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
}

@Composable
private fun WardCard(w: WardCoverage, onClick: () -> Unit) {
    val pct = w.coveragePct
    val color = coverageColor(pct)
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(14.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    w.wardName ?: "Unknown",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    "${pct.roundToInt()}%",
                    style = MaterialTheme.typography.titleMedium,
                    color = color,
                    fontWeight = FontWeight.Bold,
                )
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = "View settlements",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            LinearProgressIndicator(
                progress = { (pct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(7.dp).padding(top = 6.dp),
                color = color,
            )
            // Wards with treatment data show treated/target; wards derived from
            // settlement visitation (LGAs whose households lack ward_name) show
            // the visitation read-out instead of a misleading "0 / 0".
            val hasTreatment = w.baselineTotal > 0 || w.actualTreated > 0
            Text(
                if (hasTreatment)
                    "${formatCount(w.actualTreated)} treated / ${formatCount(w.baselineTotal)} target  ·  ${w.teams} team(s)"
                else
                    "${pct.roundToInt()}% of settlements visited · tap to view",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
        }
    }
}

@Composable
private fun SettlementCoveragePage(lga: String, ward: String, projectId: Int?, onBack: () -> Unit) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var setts by remember { mutableStateOf<List<SettlementCoverage>>(emptyList()) }

    LaunchedEffect(lga, ward) {
        loading = true
        error = null
        try {
            setts = ServiceLocator.api.coverageSettlement(lga, ward, projectId)
        } catch (e: Exception) {
            error = "Could not load settlements: ${e.message ?: "network error"}"
        }
        loading = false
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back to wards")
            }
            Text(
                "$ward · settlements",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
        }
        when {
            loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
            error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
                Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
            }
            setts.isEmpty() -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
                Text("No settlement data for this ward yet.", textAlign = TextAlign.Center)
            }
            else -> {
                val visited = setts.count { it.isVisited }
                LazyColumn(
                    Modifier.fillMaxSize().padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    item {
                        Text(
                            "$visited of ${setts.size} settlements visited",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(bottom = 2.dp),
                        )
                    }
                    items(setts) { SettlementCard(it) }
                }
            }
        }
    }
}

@Composable
private fun SettlementCard(s: SettlementCoverage) {
    val pct = s.completenessPct
    val color = coverageColor(pct)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp)) {
            Row(
                Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    s.settlementName ?: "Unknown",
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    if (s.isVisited) "Visited" else "Not visited",
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = FontWeight.Bold,
                    color = if (s.isVisited) CoverageGood else CoverageLow,
                )
            }
            LinearProgressIndicator(
                progress = { (pct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(6.dp).padding(top = 6.dp),
                color = color,
            )
            Text(
                "${pct.roundToInt()}% complete  ·  ${formatCount(s.pointCount)} GPS point(s)",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 5.dp),
            )
        }
    }
}

/** Full numbers with thousands separators (e.g. 296,237) — no k/M abbreviation. */
private fun formatCount(n: Int): String = java.text.NumberFormat.getIntegerInstance().format(n.toLong())
