package org.ehealth.eritas.feature.locate

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Looper
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/**
 * One-shot current-location read using the platform LocationManager - no
 * Google Play Services dependency. Caller must already hold a location
 * permission. Returns null if no provider is enabled.
 */
@SuppressLint("MissingPermission")
suspend fun getCurrentLocation(context: Context): Location? =
    suspendCancellableCoroutine { cont ->
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val provider = when {
            lm.isProviderEnabled(LocationManager.GPS_PROVIDER) -> LocationManager.GPS_PROVIDER
            lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER) -> LocationManager.NETWORK_PROVIDER
            else -> null
        }
        if (provider == null) {
            cont.resume(null)
            return@suspendCancellableCoroutine
        }

        val listener = object : LocationListener {
            override fun onLocationChanged(location: Location) {
                lm.removeUpdates(this)
                if (cont.isActive) cont.resume(location)
            }
            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) {}
        }

        lm.requestLocationUpdates(provider, 0L, 0f, listener, Looper.getMainLooper())

        // If we already have a recent fix, return it straight away.
        val last = lm.getLastKnownLocation(provider)
        if (last != null && cont.isActive) {
            lm.removeUpdates(listener)
            cont.resume(last)
        }
        cont.invokeOnCancellation { lm.removeUpdates(listener) }
    }
