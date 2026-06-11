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

    /**
     * Tripped by [MainActivity]'s inactivity timer after 10 minutes with no
     * user interaction (mirrors the web app's idle auto-logout). The root
     * composable observes it, clears the session, and returns to login.
     */
    var idleExpired by mutableStateOf(false)
        private set

    /** Called from the OkHttp interceptor when a 401 comes back. */
    fun onUnauthorized() { sessionExpired = true }

    /** Called by MainActivity when the 10-minute idle timer fires. */
    fun onIdleTimeout() { idleExpired = true }

    /** Called after a successful (re)login. */
    fun reset() { sessionExpired = false; idleExpired = false }
}

/**
 * Tripped when the server returns HTTP 426 (Upgrade Required) — i.e. this
 * install is below the deliberate force-update floor. The root composable shows
 * the blocking "Update required" wall instead of a raw "HTTP 426" error on a
 * screen. (Routine new releases are NOT 426 — they surface as the optional
 * "update available" prompt via the launch /version check.)
 */
object UpdateRequiredState {
    var required by mutableStateOf(false)
        private set

    fun trip() { required = true }
}
