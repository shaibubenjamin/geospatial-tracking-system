package org.ehealth.eritas.feature.map

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.model.GeoSummary
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import kotlin.math.roundToInt

/**
 * Map tab: a stats strip (Geographic-View summary, fetched in Kotlin) above a
 * WebView that LOADS the server-rendered /app/map page (MapLibre GL JS). Using
 * loadUrl on a real page - instead of injecting HTML - runs in a normal
 * browsing context so WebGL + CDN scripts work (the same reason the web
 * dashboard map renders correctly).
 */
@Composable
fun CoverageMapScreen(projectId: Int?) {
    var summary by remember { mutableStateOf<GeoSummary?>(null) }

    LaunchedEffect(projectId) {
        summary = null
        summary = runCatching { ServiceLocator.api.geoSummary(projectId) }.getOrNull()
    }

    Column(Modifier.fillMaxSize()) {
        summary?.let { GeoSummaryStrip(it) }
        // The coverage page (Leaflet map + drill-down list) carries its own
        // legend, so no Compose legend overlay here.
        MapWebView(projectId = projectId, modifier = Modifier.fillMaxWidth().weight(1f))
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun MapWebView(projectId: Int?, modifier: Modifier = Modifier) {
    // Pass the auth token in the URL fragment so the map page can fetch the
    // gated /api/app/geo/* data. Fragments aren't sent to the server or logged,
    // so the token isn't exposed; the data stays in-app only.
    val url = remember(projectId) {
        val token = ServiceLocator.tokenStore.token.orEmpty()
        BuildConfig.BASE_URL.trimEnd('/') + "/app/map" +
            (projectId?.let { "?project_id=$it" } ?: "") +
            "#token=$token"
    }
    AndroidView(
        modifier = modifier,
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                setBackgroundColor(android.graphics.Color.parseColor("#E8EAED"))
            }
        },
        // update runs on EVERY recomposition; loading the URL unconditionally
        // reloaded the WebView mid-map-init each time the summary cards arrived,
        // which can leave MapLibre's WebGL canvas blank. Only (re)load when the
        // URL actually changes (i.e. project switch).
        update = { web -> if (web.tag != url) { web.tag = url; web.loadUrl(url) } },
        onRelease = { it.destroy() },
    )
}

/** Wrapping grid of the Geographic View headline numbers, above the map. */
@Composable
private fun GeoSummaryStrip(s: GeoSummary) {
    val items = buildList {
        s.completeness?.let {
            add("Completeness" to "${it.overallCompleteness.roundToInt()}%")
            add("Visited" to "${it.visitedSettlements}/${it.totalSettlements}")
        }
        s.coverageSummary?.lga?.let { add("LGAs ≥70%" to "${it.atTarget}/${it.total}") }
        s.coverageSummary?.ward?.let { add("Wards ≥70%" to "${it.atTarget}/${it.total}") }
        s.coverageSummary?.settlement?.let { add("Settl. ≥70%" to "${it.atTarget}/${it.total}") }
    }
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items.chunked(3).forEach { row ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                row.forEach { GeoStat(it.first, it.second, Modifier.weight(1f)) }
                repeat(3 - row.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

@Composable
private fun GeoStat(label: String, value: String, modifier: Modifier = Modifier) {
    Card(modifier, shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            Text(
                value,
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(label, style = MaterialTheme.typography.labelSmall, maxLines = 1)
        }
    }
}

@Composable
private fun Legend(modifier: Modifier = Modifier) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(8.dp),
        color = MaterialTheme.colorScheme.surface,
        tonalElevation = 3.dp,
    ) {
        Row(
            Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            LegendChip(CoverageLow, "Low")
            LegendChip(CoverageMid, "Mid")
            LegendChip(CoverageGood, "≥70%")
        }
    }
}

@Composable
private fun LegendChip(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(12.dp).clip(RoundedCornerShape(2.dp)).background(color))
        Spacer(Modifier.size(4.dp))
        Text(label, style = MaterialTheme.typography.labelSmall)
    }
}
