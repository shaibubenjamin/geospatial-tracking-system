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
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.material3.Surface
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
import org.ehealth.eritas.core.model.GeoBucket
import org.ehealth.eritas.core.model.GeoCoverageSummary
import org.ehealth.eritas.core.model.GeoSummary
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
fun LgaCoverageScreen(projectId: Int?, onOpenMap: (String, String?) -> Unit = { _, _ -> }) {
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
                onOpenMap = onOpenMap,
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
    onOpenMap: (String, String?) -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var rows by remember { mutableStateOf<List<LgaCoverage>>(emptyList()) }
    var summary by remember { mutableStateOf<GeoSummary?>(null) }

    LaunchedEffect(projectId) {
        loading = true
        error = null
        try {
            rows = ServiceLocator.api.coverageLga(projectId)
        } catch (e: Exception) {
            error = "Could not load LGA coverage: ${e.message ?: "network error"}"
        }
        // For the "% at target" summary cards (moved here from the Geo tab).
        summary = runCatching { ServiceLocator.api.geoSummary(projectId) }.getOrNull()
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
            summary?.coverageSummary?.let { cs ->
                item { AtTargetCard(cs) }
            }
            item {
                Text(
                    "Coverage by LGA · tap to view wards",
                    style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.padding(bottom = 4.dp),
                )
            }
            items(rows) { row ->
                LgaRow(row, onViewMap = { row.lga?.let { onOpenMap(it, null) } }) { onOpenLga(row.lga) }
            }
        }
    }
}

/** "% at target" summary (moved here from the Geo tab so the map has room). */
@Composable
private fun AtTargetCard(cs: GeoCoverageSummary) {
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(14.dp)) {
            Text(
                "% AT TARGET",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "Share of LGAs / wards / settlements meeting the coverage threshold.",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 1.dp, bottom = 8.dp),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BucketChip("LGAs", cs.lga, Modifier.weight(1f))
                BucketChip("Wards", cs.ward, Modifier.weight(1f))
                BucketChip("Settlements", cs.settlement, Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun BucketChip(label: String, bucket: GeoBucket?, modifier: Modifier = Modifier) {
    val pct = bucket?.pct ?: 0.0
    Surface(
        modifier,
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(Modifier.padding(10.dp)) {
            Text(
                label.uppercase(),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "${pct.roundToInt()}%",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = coverageColor(pct),
            )
            Text(
                if (bucket == null) "-" else "${formatCount(bucket.atTarget)} of ${formatCount(bucket.total)}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun WardCoveragePage(
    lga: String,
    projectId: Int?,
    onBack: () -> Unit,
    onOpenMap: (String, String?) -> Unit,
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
                items(wards) { w ->
                    WardCard(
                        w,
                        onViewMap = { onOpenMap(w.lga ?: lga, null) },
                    ) { w.wardName?.let(onOpenWard) }
                }
            }
        }
    }
}

@Composable
private fun LgaRow(row: LgaCoverage, onViewMap: () -> Unit, onClick: () -> Unit) {
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
                    // trigger the card's drill-to-wards - the button consumes it).
                    IconButton(onClick = onViewMap) {
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
private fun WardCard(w: WardCoverage, onViewMap: () -> Unit, onClick: () -> Unit) {
    val pct = w.coveragePct
    val color = coverageColor(pct)
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(start = 14.dp, end = 4.dp, top = 14.dp, bottom = 14.dp)) {
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
                IconButton(onClick = onViewMap) {
                    Icon(Icons.Filled.Place, contentDescription = "View on map", tint = MaterialTheme.colorScheme.primary)
                }
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
            // settlement visitation (LGAs whose households lack ward_name) show a
            // visitation read-out instead of a misleading "0 / 0".
            val hasTreatment = w.baselineTotal > 0 || w.actualTreated > 0
            Text(
                if (hasTreatment)
                    "${formatCount(w.actualTreated)} treated / ${formatCount(w.baselineTotal)} target  ·  ${w.teams} team(s)"
                else
                    "${pct.roundToInt()}% of settlements visited",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = 6.dp),
            )
            Text(
                "Tap to view settlements",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(top = 3.dp),
            )
        }
    }
}

@Composable
private fun SettlementCoveragePage(
    lga: String,
    ward: String,
    projectId: Int?,
    onBack: () -> Unit,
    onOpenMap: (String, String?) -> Unit,
) {
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
            else -> LazyColumn(
                Modifier.fillMaxSize().padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                item {
                    Text(
                        "${setts.size} settlements · % shows completeness",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(bottom = 2.dp),
                    )
                }
                items(setts) { s ->
                    SettlementCard(s, onViewMap = { onOpenMap(s.lgaName ?: lga, s.settlementName) })
                }
            }
        }
    }
}

@Composable
private fun SettlementCard(s: SettlementCoverage, onViewMap: () -> Unit) {
    val pct = s.completenessPct
    val color = coverageColor(pct)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(start = 12.dp, end = 4.dp, top = 12.dp, bottom = 12.dp)) {
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
                // No more Visited/Not-visited badge - it contradicted the % (a
                // settlement could read 'Not visited' at 20%, or 'Visited' at 0%).
                // The completeness % is the single source of truth now.
                Text(
                    "${pct.roundToInt()}%",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = color,
                )
                IconButton(onClick = onViewMap) {
                    Icon(Icons.Filled.Place, contentDescription = "View on map", tint = MaterialTheme.colorScheme.primary)
                }
            }
            LinearProgressIndicator(
                progress = { (pct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(6.dp).padding(top = 6.dp, end = 8.dp),
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

/** Full numbers with thousands separators (e.g. 296,237) - no k/M abbreviation. */
private fun formatCount(n: Int): String = java.text.NumberFormat.getIntegerInstance().format(n.toLong())
