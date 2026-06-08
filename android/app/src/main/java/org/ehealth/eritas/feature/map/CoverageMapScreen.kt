package org.ehealth.eritas.feature.map

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import org.ehealth.eritas.core.model.GeoSummary
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import kotlin.math.roundToInt

/**
 * Coverage map rendered with MapLibre GL JS inside a WebView — the same map
 * engine the web dashboard uses. This avoids the MapLibre Native Android SDK,
 * whose native renderer was crashing on-device, and keeps the APK small (no
 * bundled native map libraries). The ward polygons + coverage come from
 * /api/app/geo/wards (fetched, authenticated, in Kotlin) and are injected into
 * the page; the JS colors wards by coverage and fits to the project's extent.
 */
@Composable
fun CoverageMapScreen(projectId: Int?) {
    var geoJson by remember { mutableStateOf<String?>(null) }
    var status by remember { mutableStateOf<String?>("Loading boundaries…") }
    var summary by remember { mutableStateOf<GeoSummary?>(null) }

    LaunchedEffect(projectId) {
        status = "Loading boundaries…"
        geoJson = null
        summary = null
        try {
            val gj = ServiceLocator.api.wardsGeoJson(projectId).string()
            val n = Regex("\"geometry\"").findAll(gj).count()
            status = if (n == 0) "No boundaries for this project" else null
            geoJson = gj
        } catch (e: Exception) {
            // Surface the real cause so field issues are diagnosable.
            val detail = (e as? retrofit2.HttpException)?.let { "HTTP ${it.code()}" }
                ?: (e.message?.take(80) ?: e.javaClass.simpleName)
            status = "Couldn't load boundaries · $detail"
            geoJson = null
        }
        summary = runCatching { ServiceLocator.api.geoSummary(projectId) }.getOrNull()
    }

    Column(Modifier.fillMaxSize()) {
        summary?.let { GeoSummaryStrip(it) }
        Box(Modifier.fillMaxWidth().weight(1f)) {
            MapWebView(geoJson = geoJson, modifier = Modifier.fillMaxSize())
            Legend(Modifier.align(Alignment.BottomStart).padding(12.dp))
            status?.let { msg ->
                Surface(
                    Modifier.align(Alignment.TopCenter).padding(12.dp),
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surface,
                    tonalElevation = 3.dp,
                ) {
                    Text(
                        msg,
                        Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        style = MaterialTheme.typography.labelMedium,
                    )
                }
            }
        }
    }
}

/** Wrapping grid of the Geographic View headline numbers, above the map.
 *  Fixed 3-per-row so everything is visible without sideways scrolling. */
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

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun MapWebView(geoJson: String?, modifier: Modifier = Modifier) {
    val html = remember(geoJson) { buildMapHtml(geoJson) }
    AndroidView(
        modifier = modifier,
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                setBackgroundColor(android.graphics.Color.parseColor("#E8EAED"))
            }
        },
        update = { web ->
            // Base64-encode: loadDataWithBaseURL with utf-8 mis-parses '#' and
            // '%' (our HTML is full of '#' hex colors), which silently blanks
            // the page. base64 avoids that. A real https baseUrl gives the
            // page a proper origin so the CDN scripts + WebGL work.
            val b64 = android.util.Base64.encodeToString(
                html.toByteArray(Charsets.UTF_8), android.util.Base64.NO_PADDING,
            )
            web.loadDataWithBaseURL(
                org.ehealth.eritas.BuildConfig.BASE_URL, b64, "text/html", "base64", null,
            )
        },
        onRelease = { it.destroy() },
    )
}

private fun buildMapHtml(geoJson: String?): String {
    val data = geoJson?.takeIf { it.isNotBlank() } ?: "null"
    return """
<!DOCTYPE html>
<html>
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
  <link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet">
  <script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
  <style>html,body,#map{margin:0;padding:0;height:100%;width:100%}</style>
</head>
<body>
  <div id="map"></div>
  <script>
    var data = $data;
    var map = new maplibregl.Map({
      container: 'map',
      style: {
        version: 8,
        sources: { carto: { type: 'raster',
          tiles: ['https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png',
                  'https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png'],
          tileSize: 256, attribution: '© OpenStreetMap © CARTO' } },
        layers: [
          { id: 'bg', type: 'background', paint: { 'background-color': '#E8EAED' } },
          { id: 'carto', type: 'raster', source: 'carto' }
        ]
      },
      center: [5.24, 13.06], zoom: 6
    });
    map.on('load', function () {
      if (data && data.features && data.features.length) {
        map.addSource('wards', { type: 'geojson', data: data });
        map.addLayer({ id: 'wards-fill', type: 'fill', source: 'wards', paint: {
          'fill-color': ['interpolate', ['linear'], ['get', 'coverage_pct'],
            0, '#C62828', 50, '#F9A825', 70, '#66BB6A', 100, '#2E7D32'],
          'fill-opacity': 0.55 } });
        map.addLayer({ id: 'wards-line', type: 'line', source: 'wards', paint: {
          'line-color': '#37474F', 'line-width': 0.8 } });
        var b = new maplibregl.LngLatBounds();
        function ext(c){ if(typeof c[0]==='number'){ b.extend(c);} else { c.forEach(ext);} }
        data.features.forEach(function(f){ if(f.geometry) ext(f.geometry.coordinates); });
        if(!b.isEmpty()) map.fitBounds(b, { padding: 28, duration: 0 });
      }
    });
  </script>
</body>
</html>
"""
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
