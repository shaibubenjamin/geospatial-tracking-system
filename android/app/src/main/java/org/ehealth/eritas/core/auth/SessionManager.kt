package org.ehealth.eritas.core.auth

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * Global session signal. The networking layer flips [sessionExpired] to true
 * when the server rejects our token (HTTP 401 — e.g. the 8-hour JWT lapsed).
 * The root composable observes it and drops the user back to the login screen,
 * instead of every screen surfacing a raw "HTTP 401". Reset after a fresh login.
 */
object SessionManager {
    var sessionExpired by mutableStateOf(false)
        private set

    /** Called from the OkHttp interceptor when a 401 comes back. */
    fun onUnauthorized() { sessionExpired = true }

    /** Called after a successful (re)login. */
    fun reset() { sessionExpired = false }
}
