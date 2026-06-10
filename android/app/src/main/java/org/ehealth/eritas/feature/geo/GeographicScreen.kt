package org.ehealth.eritas.feature.geo

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.core.model.GeoBucket
import org.ehealth.eritas.core.model.GeoSummary
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.feature.web.AppWebScreen
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import kotlin.math.roundToInt

/**
 * Geographic view — headline coverage numbers (from /api/app/geo/summary) above
 * the interactive Leaflet coverage map (/app/map in a WebView). The cards give
 * the at-a-glance % the map can't; the map gives the where.
 */
@Composable
fun GeographicScreen(projectId: Int?, focusLga: String? = null) {
    var summary by remember { mutableStateOf<GeoSummary?>(null) }

    LaunchedEffect(projectId) {
        summary = runCatching { ServiceLocator.api.geoSummary(projectId) }.getOrNull()
    }

    Column(Modifier.fillMaxSize()) {
        GeoSummaryHeader(summary)
        // The map fills whatever space the header leaves; focusLga (from a
        // Coverage row's map pin) zooms it to that LGA.
        AppWebScreen(
            path = "/app/map",
            projectId = projectId,
            focusLga = focusLga,
            modifier = Modifier.fillMaxWidth().weight(1f),
        )
    }
}

@Composable
private fun GeoSummaryHeader(summary: GeoSummary?) {
    Surface(
        Modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
        shadowElevation = 2.dp,
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 12.dp)) {
            val comp = summary?.completeness
            val visitPct = comp?.visitationPct ?: 0.0
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                Text(
                    "Settlements visited",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    if (comp == null) "—" else "${visitPct.roundToInt()}%",
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.Bold,
                    color = covColor(visitPct),
                )
            }
            LinearProgressIndicator(
                progress = { (visitPct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().height(7.dp).padding(top = 4.dp),
                color = covColor(visitPct),
                trackColor = MaterialTheme.colorScheme.surfaceVariant,
            )
            if (comp != null) {
                Text(
                    "${fmt(comp.visitedSettlements)} of ${fmt(comp.totalSettlements)} settlements have " +
                        "at least one GPS submission.",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 3.dp),
                )
            }
            val cs = summary?.coverageSummary
            if (cs != null) {
                Spacer(Modifier.height(12.dp))
                Text(
                    "% AT TARGET",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    // The distinction that confused: visited ≠ at target.
                    "Share of areas that MEET the coverage threshold — not the same " +
                        "as 'visited' above (a settlement can be visited but still " +
                        "below target).",
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
}

private fun fmt(n: Int): String = java.text.NumberFormat.getIntegerInstance().format(n.toLong())

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
                color = covColor(pct),
            )
            Text(
                if (bucket == null) "—" else "${fmt(bucket.atTarget)} of ${fmt(bucket.total)}",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun covColor(pct: Double): Color = when {
    pct >= 70 -> CoverageGood
    pct >= 40 -> CoverageMid
    else -> CoverageLow
}
