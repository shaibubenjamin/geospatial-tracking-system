package org.ehealth.eritas.core.net

import okhttp3.Interceptor
import okhttp3.Response
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.auth.SessionManager
import org.ehealth.eritas.core.auth.TokenStore
import org.ehealth.eritas.core.auth.UpdateRequiredState

/**
 * Stamps every outgoing request with the installed app's versionCode. This is
 * what the server-side force-update gate compares against MIN_VERSION_CODE —
 * see app/main.py `enforce_app_version` and docs/apk-app-blueprint.md.
 */
class VersionInterceptor : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request().newBuilder()
            .header("X-App-Version-Code", BuildConfig.VERSION_CODE.toString())
            .build()
        return chain.proceed(request)
    }
}

/** Attaches the stored JWT as a Bearer token when one is present. */
class AuthInterceptor(private val tokenStore: TokenStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenStore.token
        val request = if (token.isNullOrBlank()) {
            chain.request()
        } else {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        }
        return chain.proceed(request)
    }
}

/**
 * Turns an expired/invalid token into a clean logout. On HTTP 401 the stored
 * token is cleared and [SessionManager] is tripped so the root composable
 * returns to the login screen — instead of every screen showing a raw
 * "HTTP 401". (426 / force-update is handled separately by the update gate.)
 */
class UnauthorizedInterceptor(private val tokenStore: TokenStore) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val response = chain.proceed(chain.request())
        when (response.code) {
            401 -> {
                tokenStore.clear()
                // Forget the selected project too, so the next account doesn't
                // inherit a round it can't access (e.g. an out-of-state LGA login).
                ServiceLocator.projectStore.clear()
                SessionManager.onUnauthorized()
            }
            // 426 Upgrade Required: this install is below the force-update floor.
            // Trip the global signal so the UI shows the "Update required" wall
            // instead of a raw "HTTP 426" error on whatever screen made the call.
            426 -> UpdateRequiredState.trip()
        }
        return response
    }
}
