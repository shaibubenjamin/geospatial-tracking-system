package org.ehealth.eritas.feature.web

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.net.ServiceLocator

/**
 * Hosts a server-rendered app page (e.g. /app/dashboard, /app/map) in a WebView.
 *
 * Loads via loadUrl so it runs in a normal browsing context (CDN scripts -
 * Leaflet, Chart.js - work). The auth token rides in the URL fragment (#token=…)
 * so it's never sent to the server or logged, and the selected project is a
 * query param. Re-loads ONLY when the URL changes (project switch), not on every
 * recompose - reloading mid-render left the WebGL/canvas blank before.
 */
@SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
@Composable
fun AppWebScreen(
    path: String,
    projectId: Int?,
    modifier: Modifier = Modifier.fillMaxSize(),
    // When set, the page can call `Native.openMap(lgacode)` to jump to the map
    // (the dashboard's LGA-coverage rows use this). Runs on the main thread.
    onOpenMap: ((String) -> Unit)? = null,
    // When set, appended as ?focus=<lga> so the map page zooms to that LGA.
    focusLga: String? = null,
    // When set, appended as &focus_settlement=<name> so the map zooms onto that
    // settlement (URL-encoded - names have spaces/apostrophes).
    focusSettlement: String? = null,
    // When true, append ?app=1 (the wrapped /mda reads this to switch to its
    // guarded mobile layout).
    appMode: Boolean = false,
) {
    val url = remember(path, projectId, focusLga, focusSettlement, appMode) {
        val token = ServiceLocator.tokenStore.token.orEmpty()
        val params = buildList {
            if (appMode) add("app=1")
            projectId?.let { add("project_id=$it") }
            focusLga?.let { add("focus=$it") }
            focusSettlement?.let { add("focus_settlement=" + java.net.URLEncoder.encode(it, "UTF-8")) }
        }.joinToString("&")
        BuildConfig.BASE_URL.trimEnd('/') + path +
            (if (params.isNotEmpty()) "?$params" else "") +
            "#token=$token"
    }
    AndroidView(
        modifier = modifier,
        factory = { context ->
            WebView(context).apply {
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                setBackgroundColor(android.graphics.Color.parseColor("#F3F4F6"))
                if (onOpenMap != null) {
                    addJavascriptInterface(object {
                        @android.webkit.JavascriptInterface
                        fun openMap(lgacode: String) { post { onOpenMap(lgacode) } }
                    }, "Native")
                }
            }
        },
        update = { web -> if (web.tag != url) { web.tag = url; web.loadUrl(url) } },
        onRelease = { it.destroy() },
    )
}
