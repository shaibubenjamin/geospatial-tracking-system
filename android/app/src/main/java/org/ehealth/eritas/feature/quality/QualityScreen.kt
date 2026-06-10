package org.ehealth.eritas.feature.quality

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import org.ehealth.eritas.core.model.LgaCoverage
import org.ehealth.eritas.core.model.OverviewDto
import org.ehealth.eritas.core.model.TrendPoint
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import org.ehealth.eritas.ui.EritasGreen
import kotlin.math.roundToInt

/**
 * Quality & performance — data-quality (QC) metrics, team metrics, and daily
 * trends for the selected campaign. Built from /api/app/{overview,trends/daily,
 * coverage/lga}; no map, no WebView.
 */
@Composable
fun QualityScreen(projectId: Int?) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var ov by remember { mutableStateOf<OverviewDto?>(null) }
    var trend by remember { mutableStateOf<List<TrendPoint>>(emptyList()) }
    var lgas by remember { mutableStateOf<List<LgaCoverage>>(emptyList()) }

    LaunchedEffect(projectId) {
        loading = true
        error = null
        try {
            ov = ServiceLocator.api.overview(projectId)
            trend = ServiceLocator.api.trendsDaily(projectId)
            lgas = ServiceLocator.api.coverageLga(projectId)
        } catch (e: Exception) {
            error = "Could not load quality metrics: ${e.message ?: "network error"}"
        }
        loading = false
    }

    when {
        loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
            Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        }
        ov != null -> Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            val o = ov!!

            SectionTitle("Data quality")
            val qc = listOf(
                Metric("QC flags", formatCount(o.totalQcFlags), Icons.Filled.Flag, flagColor(o.totalQcFlags > 0)),
                Metric("Error rate", "${o.errorRatePct.roundToInt()}%", Icons.Filled.ErrorOutline, rateColor(o.errorRatePct)),
                Metric("Refusals", formatCount(o.refusals), Icons.Filled.Block, EritasGreen),
                Metric("Fast forms", formatCount(o.fastForms), Icons.Filled.Bolt, EritasGreen),
                Metric("GPS outside LGA", formatCount(o.gpsOutsideLga), Icons.Filled.Place, flagColor(o.gpsOutsideLga > 0)),
            )
            MetricGrid(qc)

            SectionTitle("Teams")
            val avgPerTeam = if (o.teamsActive > 0) o.totalForms / o.teamsActive else 0
            val teams = listOf(
                Metric("Active teams", formatCount(o.teamsActive), Icons.Filled.Groups, EritasGreen),
                Metric("Avg forms / team", formatCount(avgPerTeam), Icons.Filled.Description, EritasGreen),
            )
            MetricGrid(teams)
            if (lgas.isNotEmpty()) TeamsByLgaCard(lgas)

            if (trend.size >= 2) {
                SectionTitle("Daily trend")
                DailyTrendCard(trend)
            }
            Spacer(Modifier.size(4.dp))
        }
    }
}

private data class Metric(val label: String, val value: String, val icon: ImageVector, val accent: Color)

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.Bold,
        color = EritasGreen,
    )
}

@Composable
private fun MetricGrid(items: List<Metric>) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        items.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { m -> MetricCard(m, Modifier.weight(1f)) }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

@Composable
private fun MetricCard(m: Metric, modifier: Modifier = Modifier) {
    Card(modifier, shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(m.icon, contentDescription = null, tint = m.accent, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(6.dp))
                Text(
                    m.label.uppercase(),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Spacer(Modifier.height(8.dp))
            Text(
                m.value,
                fontSize = 22.sp,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
    }
}

@Composable
private fun TeamsByLgaCard(lgas: List<LgaCoverage>) {
    val top = lgas.sortedByDescending { it.teams }.take(6)
    val maxTeams = (top.maxOfOrNull { it.teams } ?: 1).coerceAtLeast(1)
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Active teams by LGA",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.height(8.dp))
            top.forEach { row ->
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        row.lga ?: "—",
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.width(110.dp),
                        maxLines = 1,
                    )
                    Box(
                        Modifier
                            .weight(1f)
                            .height(14.dp)
                            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(7.dp)),
                    ) {
                        Box(
                            Modifier
                                .fillMaxWidth(row.teams.toFloat() / maxTeams)
                                .height(14.dp)
                                .background(EritasGreen, RoundedCornerShape(7.dp)),
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(
                        "${row.teams}",
                        style = MaterialTheme.typography.bodySmall,
                        fontWeight = FontWeight.Bold,
                    )
                }
            }
        }
    }
}

@Composable
private fun DailyTrendCard(trend: List<TrendPoint>) {
    val maxForms = (trend.maxOfOrNull { it.forms } ?: 1).coerceAtLeast(1)
    val totalForms = trend.sumOf { it.forms }
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Forms submitted per day",
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                "${formatCount(totalForms)} forms over ${trend.size} days",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(12.dp))
            Canvas(Modifier.fillMaxWidth().height(140.dp)) {
                val n = trend.size
                val gap = 6f
                val barW = ((size.width - gap * (n - 1)) / n).coerceAtLeast(2f)
                trend.forEachIndexed { i, p ->
                    val bh = (p.forms.toFloat() / maxForms) * (size.height * 0.92f)
                    val x = i * (barW + gap)
                    drawRect(
                        color = EritasGreen,
                        topLeft = androidx.compose.ui.geometry.Offset(x, size.height - bh),
                        size = androidx.compose.ui.geometry.Size(barW, bh),
                    )
                }
            }
            val dates = trend.mapNotNull { it.date }
            if (dates.isNotEmpty()) {
                Row(
                    Modifier.fillMaxWidth().padding(top = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Text(
                        dates.first().takeLast(5),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        dates.last().takeLast(5),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

private fun flagColor(bad: Boolean): Color = if (bad) CoverageMid else CoverageGood
private fun rateColor(pct: Double): Color = when {
    pct >= 10 -> CoverageLow
    pct >= 5 -> CoverageMid
    else -> CoverageGood
}

private fun formatCount(n: Int): String = java.text.NumberFormat.getIntegerInstance().format(n.toLong())
