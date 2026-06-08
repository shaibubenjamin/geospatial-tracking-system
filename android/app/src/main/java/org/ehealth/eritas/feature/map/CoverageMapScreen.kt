package org.ehealth.eritas.feature.map

import android.graphics.Color
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
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
import androidx.compose.ui.graphics.Color as ComposeColor
import androidx.compose.ui.platform.LocalLifecycleOwner
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.ui.CoverageGood
import org.ehealth.eritas.ui.CoverageLow
import org.ehealth.eritas.ui.CoverageMid
import org.json.JSONArray
import org.json.JSONObject
import org.maplibre.android.camera.CameraPosition
import org.maplibre.android.camera.CameraUpdateFactory
import org.maplibre.android.geometry.LatLng
import org.maplibre.android.geometry.LatLngBounds
import org.maplibre.android.maps.MapLibreMap
import kotlin.math.max
import kotlin.math.min
import org.maplibre.android.maps.MapView
import org.maplibre.android.maps.Style
import org.maplibre.android.style.expressions.Expression
import org.maplibre.android.style.layers.FillLayer
import org.maplibre.android.style.layers.LineLayer
import org.maplibre.android.style.layers.PropertyFactory
import org.maplibre.android.style.sources.GeoJsonSource

// Keyless raster base map (CARTO Voyager). OSM's tile server blocks app
// clients without an approved User-Agent, leaving the map blank — CARTO's
// public basemap tiles are reliable for this use. A background layer keeps the
// map neutral if tiles are slow/unreachable. For a high-traffic rollout, move
// to a tile provider with an explicit usage agreement / API key.
private const val BASE_STYLE_JSON = """
{
  "version": 8,
  "sources": {
    "carto": {
      "type": "raster",
      "tiles": [
        "https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        "https://b.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
        "https://c.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"
      ],
      "tileSize": 256,
      "attribution": "© OpenStreetMap © CARTO"
    }
  },
  "layers": [
    { "id": "bg", "type": "background", "paint": { "background-color": "#E8EAED" } },
    { "id": "carto", "type": "raster", "source": "carto" }
  ]
}
"""

private const val WARD_SOURCE = "wards"

@Composable
fun CoverageMapScreen(projectId: Int?) {
    val lifecycleOwner = LocalLifecycleOwner.current
    var mapRef by remember { mutableStateOf<MapLibreMap?>(null) }
    var geoJson by remember { mutableStateOf<String?>(null) }

    var boundariesStatus by remember { mutableStateOf<String?>(null) }

    // Fetch ward polygons + coverage for the selected project as raw GeoJSON.
    LaunchedEffect(projectId) {
        boundariesStatus = "Loading boundaries…"
        geoJson = try {
            val gj = ServiceLocator.api.wardsGeoJson(projectId).string()
            val n = Regex("\"geometry\"").findAll(gj).count()
            boundariesStatus = if (n == 0) "No boundaries for this project" else null
            gj
        } catch (_: Exception) {
            boundariesStatus = "Couldn't load boundaries"
            null
        }
    }

    // Re-style whenever the data or the map becomes available.
    LaunchedEffect(mapRef, geoJson) {
        val map = mapRef ?: return@LaunchedEffect
        runCatching { applyCoverageStyle(map, geoJson) }
    }

    val mapView = remember { mutableStateOf<MapView?>(null) }
    var mapError by remember { mutableStateOf<String?>(null) }

    Box(Modifier.fillMaxSize()) {
        if (mapError != null) {
            Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                Text(
                    "The map couldn't load on this device. " +
                        "Use the Coverage and My Area tabs instead.",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        } else {
            AndroidView(
                factory = { context ->
                    try {
                        MapView(context).apply {
                            onCreate(null)
                            mapView.value = this
                            getMapAsync { map ->
                                runCatching {
                                    map.cameraPosition = CameraPosition.Builder()
                                        .target(LatLng(13.06, 5.24)) // Sokoto default
                                        .zoom(7.0)
                                        .build()
                                    mapRef = map
                                }.onFailure { mapError = it.message ?: "map error" }
                            }
                        }
                    } catch (t: Throwable) {
                        mapError = t.message ?: "map init failed"
                        android.view.View(context)
                    }
                },
                modifier = Modifier.fillMaxSize(),
            )
            Legend(Modifier.align(Alignment.BottomStart).padding(12.dp))
            boundariesStatus?.let { msg ->
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

    // Forward Compose lifecycle to the MapView (required by MapLibre).
    DisposableEffect(lifecycleOwner, mapView.value) {
        val mv = mapView.value
        val observer = LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_START -> mv?.onStart()
                Lifecycle.Event.ON_RESUME -> mv?.onResume()
                Lifecycle.Event.ON_PAUSE -> mv?.onPause()
                Lifecycle.Event.ON_STOP -> mv?.onStop()
                Lifecycle.Event.ON_DESTROY -> mv?.onDestroy()
                else -> {}
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose {
            lifecycleOwner.lifecycle.removeObserver(observer)
            mv?.onDestroy()
        }
    }
}

private fun applyCoverageStyle(map: MapLibreMap, geoJson: String?) {
    val builder = Style.Builder().fromJson(BASE_STYLE_JSON)
    if (geoJson != null) {
        builder.withSource(GeoJsonSource(WARD_SOURCE, geoJson))
        val coverageColor = Expression.interpolate(
            Expression.linear(),
            Expression.get("coverage_pct"),
            Expression.stop(0, Expression.color(Color.parseColor("#C62828"))),
            Expression.stop(50, Expression.color(Color.parseColor("#F9A825"))),
            Expression.stop(70, Expression.color(Color.parseColor("#66BB6A"))),
            Expression.stop(100, Expression.color(Color.parseColor("#2E7D32"))),
        )
        builder.withLayer(
            FillLayer("wards-fill", WARD_SOURCE).withProperties(
                PropertyFactory.fillColor(coverageColor),
                PropertyFactory.fillOpacity(0.55f),
            )
        )
        builder.withLayer(
            LineLayer("wards-line", WARD_SOURCE).withProperties(
                PropertyFactory.lineColor(Color.parseColor("#37474F")),
                PropertyFactory.lineWidth(0.8f),
            )
        )
    }
    map.setStyle(builder) {
        // Once the style (and the project's boundary source) is loaded, fit the
        // camera to the actual extent of the selected project's boundaries —
        // so the map works for ANY state/round, not a hardcoded location.
        if (geoJson != null) {
            boundsFromGeoJson(geoJson)?.let { bounds ->
                runCatching {
                    map.easeCamera(CameraUpdateFactory.newLatLngBounds(bounds, 48), 600)
                }
            }
        }
    }
}

/** Compute the lat/lng extent of every polygon in a GeoJSON FeatureCollection
 *  so the camera can frame the selected project's boundaries. */
private fun boundsFromGeoJson(geoJson: String): LatLngBounds? {
    return try {
        val features = JSONObject(geoJson).optJSONArray("features") ?: return null
        var minLat = 90.0; var maxLat = -90.0; var minLon = 180.0; var maxLon = -180.0
        var found = false

        fun walk(arr: JSONArray) {
            if (arr.length() == 2 && arr.opt(0) is Number && arr.opt(1) is Number) {
                val lon = arr.getDouble(0); val lat = arr.getDouble(1)
                minLat = min(minLat, lat); maxLat = max(maxLat, lat)
                minLon = min(minLon, lon); maxLon = max(maxLon, lon)
                found = true
                return
            }
            for (i in 0 until arr.length()) (arr.opt(i) as? JSONArray)?.let { walk(it) }
        }

        for (i in 0 until features.length()) {
            val coords = features.getJSONObject(i)
                .optJSONObject("geometry")?.optJSONArray("coordinates") ?: continue
            walk(coords)
        }
        if (found) LatLngBounds.from(maxLat, maxLon, minLat, minLon) else null
    } catch (_: Exception) {
        null
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
private fun LegendChip(color: ComposeColor, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(12.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(color)
        )
        Spacer(Modifier.size(4.dp))
        Text(label, style = MaterialTheme.typography.labelSmall)
    }
}
