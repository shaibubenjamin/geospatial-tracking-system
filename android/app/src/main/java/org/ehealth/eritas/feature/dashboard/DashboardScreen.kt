package org.ehealth.eritas.feature.dashboard

import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Block
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.Favorite
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Groups
import androidx.compose.material.icons.filled.Place
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
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
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.core.model.OverviewDto
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import org.ehealth.eritas.ui.EritasGreen
import kotlin.math.roundToInt

private data class Stat(val label: String, val value: String, val icon: ImageVector)

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
        loading -> Box(Modifier.fillMaxSize(), Alignment.Center) { CircularProgressIndicator() }
        error != null -> Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
            Text(error!!, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
        }
        data != null -> DashboardContent(data!!, projectId)
    }
}

@Composable
private fun DashboardContent(d: OverviewDto, projectId: Int?) {
    val day = d.currentCampaignDay?.let { cur ->
        d.plannedDurationDays?.let { tot -> "$cur / $tot" } ?: cur.toString()
    } ?: "—"
    val avgPerTeam = if (d.teamsActive > 0) (d.totalForms / d.teamsActive).toString() else "—"
    val stats = listOf(
        Stat("Children treated", formatCount(d.totalTreated), Icons.Filled.Favorite),
        Stat("Forms submitted", formatCount(d.totalForms), Icons.Filled.Description),
        Stat("Teams active", formatCount(d.teamsActive), Icons.Filled.Groups),
        Stat("LGAs covered", d.lgasCovered.toString(), Icons.Filled.Place),
        Stat("Campaign day", day, Icons.Filled.Event),
        Stat("QC flags", formatCount(d.totalQcFlags), Icons.Filled.Flag),
        Stat("Error rate", "${d.errorRatePct.roundToInt()}%", Icons.Filled.ErrorOutline),
        Stat("Refusals", formatCount(d.refusals), Icons.Filled.Block),
        Stat("Avg forms/team", avgPerTeam, Icons.Filled.Groups),
        Stat("GPS outside LGA", formatCount(d.gpsOutsideLga), Icons.Filled.Place),
        Stat("Fast forms", formatCount(d.fastForms), Icons.Filled.Description),
        // Campaign DURATION (planned window length, e.g. 5 days) — NOT the count
        // of distinct submission days (11), which read as wrong for the 5-day R5
        // campaign. Mirrors the web, which drives this off the planned window.
        Stat(
            "Campaign duration",
            d.plannedDurationDays?.let { "$it days" }
                ?: d.currentCampaignDay?.let { "$it days" } ?: "—",
            Icons.Filled.Event,
        ),
    )

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        CoverageHero(d.coveragePct)
        stats.chunked(2).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                row.forEach { StatCard(it, Modifier.weight(1f)) }
                if (row.size == 1) Spacer(Modifier.weight(1f))
            }
        }
        TrendCard(projectId, d.baselineTotal)
        Spacer(Modifier.size(4.dp))
    }
}

@Composable
private fun TrendCard(projectId: Int?, baseline: Int) {
    var cum by remember { mutableStateOf<List<Pair<String?, Int>>>(emptyList()) }
    LaunchedEffect(projectId) {
        val t = runCatching { ServiceLocator.api.trendsDaily(projectId) }.getOrNull().orEmpty()
        var c = 0
        cum = t.map { c += it.treated; it.date to c }
    }
    Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(
                "Cumulative coverage over campaign days",
                style = MaterialTheme.typography.titleSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (cum.isEmpty()) {
                Text("No daily data yet.", style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 8.dp))
            } else {
                val maxV = (if (baseline > 0) baseline else (cum.maxOfOrNull { it.second } ?: 1)).coerceAtLeast(1)
                Canvas(Modifier.fillMaxWidth().height(120.dp).padding(top = 8.dp)) {
                    val n = cum.size; val w = size.width; val h = size.height; val pad = 6f
                    fun px(i: Int) = pad + if (n <= 1) 0f else i * (w - 2 * pad) / (n - 1)
                    fun py(v: Int) = h - pad - (v.toFloat() / maxV) * (h - 2 * pad)
                    val line = Path(); val area = Path(); area.moveTo(px(0), h - pad)
                    cum.forEachIndexed { i, p ->
                        val xx = px(i); val yy = py(p.second)
                        if (i == 0) line.moveTo(xx, yy) else line.lineTo(xx, yy)
                        area.lineTo(xx, yy)
                    }
                    area.lineTo(px(n - 1), h - pad); area.close()
                    drawPath(area, color = EritasGreen.copy(alpha = 0.18f))
                    drawPath(line, color = EritasGreen, style = Stroke(width = 3f))
                    cum.forEachIndexed { i, p -> drawCircle(EritasGreen, 3f, Offset(px(i), py(p.second))) }
                }
                // Daily date labels along the x-axis (sampled so they don't
                // crowd on a phone — shows MM-DD, e.g. 05-19).
                val dates = cum.mapNotNull { it.first }
                if (dates.isNotEmpty()) {
                    val step = ((dates.size + 5) / 6).coerceAtLeast(1)
                    val shown = dates.filterIndexed { i, _ -> i % step == 0 || i == dates.lastIndex }
                    Row(
                        Modifier.fillMaxWidth().padding(top = 2.dp),
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        shown.forEach {
                            Text(
                                it.takeLast(5),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
                val last = cum.last().second
                val lab = if (baseline > 0) "${(100.0 * last / maxV).roundToInt()}% of target"
                          else "${formatCount(last)} treated"
                val range = listOfNotNull(cum.first().first, cum.last().first)
                    .map { it.takeLast(5) }.joinToString(" → ")
                Text(
                    "${cum.size} day(s) · $lab" + if (range.isNotBlank()) "  ($range)" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun CoverageHero(coveragePct: Double) {
    val color = when {
        coveragePct >= 70 -> CoverageGood
        coveragePct >= 40 -> CoverageMid
        else -> CoverageLow
    }
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = color),
        shape = RoundedCornerShape(18.dp),
    ) {
        Column(Modifier.padding(20.dp)) {
            Text(
                "Administrative coverage",
                style = MaterialTheme.typography.labelLarge,
                color = Color.White.copy(alpha = 0.9f),
            )
            Text(
                "${coveragePct.roundToInt()}%",
                style = MaterialTheme.typography.displayMedium,
                fontWeight = FontWeight.Bold,
                color = Color.White,
            )
            Text(
                "Children treated ÷ baseline target · 80% is the protective threshold",
                style = MaterialTheme.typography.bodySmall,
                color = Color.White.copy(alpha = 0.9f),
            )
            LinearProgressIndicator(
                progress = { (coveragePct / 100.0).coerceIn(0.0, 1.0).toFloat() },
                modifier = Modifier.fillMaxWidth().padding(top = 12.dp),
                color = Color.White,
                trackColor = Color.White.copy(alpha = 0.3f),
            )
        }
    }
}

@Composable
private fun StatCard(stat: Stat, modifier: Modifier = Modifier) {
    Card(modifier, shape = RoundedCornerShape(16.dp)) {
        Column(Modifier.padding(16.dp)) {
            Icon(
                stat.icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(22.dp),
            )
            Spacer(Modifier.size(8.dp))
            Text(
                stat.value,
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onSurface,
            )
            Text(
                stat.label,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

private fun formatCount(n: Int): String = when {
    n >= 1_000_000 -> "${(n / 100_000) / 10.0}M"
    n >= 1_000 -> "${(n / 100) / 10.0}k"
    else -> n.toString()
}
