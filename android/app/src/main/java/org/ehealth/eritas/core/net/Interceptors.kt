package org.ehealth.eritas.core.net

import okhttp3.Interceptor
import okhttp3.Response
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.auth.TokenStore

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
