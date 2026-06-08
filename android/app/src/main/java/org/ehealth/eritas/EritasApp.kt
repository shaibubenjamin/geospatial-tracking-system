package org.ehealth.eritas

import android.app.Application
import org.ehealth.eritas.core.net.ServiceLocator
import org.maplibre.android.MapLibre

class EritasApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // MapLibre Native must be initialised before any MapView is inflated.
        // No API key needed — we use a keyless raster (OSM) style.
        MapLibre.getInstance(this)
        ServiceLocator.init(this)
    }
}
