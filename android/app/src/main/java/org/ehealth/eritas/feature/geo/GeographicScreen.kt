package org.ehealth.eritas.feature.geo

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
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
fun GeographicScreen(projectId: Int?, focusLga: String? = null, focusSettlement: String? = null) {
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
            focusSettlement = focusSettlement,
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
            // The LGA/Ward/Settlement "at target" cards moved to the LGA Coverage
            // tab so the map has room. The settlements-visited bar above stays.
        }
    }
}

private fun fmt(n: Int): String = java.text.NumberFormat.getIntegerInstance().format(n.toLong())

private fun covColor(pct: Double): Color = when {
    pct >= 70 -> CoverageGood
    pct >= 40 -> CoverageMid
    else -> CoverageLow
}
