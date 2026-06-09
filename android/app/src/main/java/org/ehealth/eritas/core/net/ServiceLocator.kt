package org.ehealth.eritas.core.net

import android.content.Context
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import org.ehealth.eritas.BuildConfig
import org.ehealth.eritas.core.auth.TokenStore
import org.ehealth.eritas.core.project.ProjectStore
import retrofit2.Retrofit
import retrofit2.converter.moshi.MoshiConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Tiny manual dependency container. Initialised once from EritasApp.onCreate
 * and read everywhere via [get]. Keeps the pilot free of a DI framework.
 */
object ServiceLocator {

    lateinit var tokenStore: TokenStore
        private set
    lateinit var projectStore: ProjectStore
        private set
    lateinit var api: Api
        private set

    fun init(context: Context) {
        if (::api.isInitialized) return
        val appContext = context.applicationContext
        tokenStore = TokenStore(appContext)
        projectStore = ProjectStore(appContext)

        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.BASIC
            } else {
                HttpLoggingInterceptor.Level.NONE
            }
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(VersionInterceptor())
            .addInterceptor(AuthInterceptor(tokenStore))
            .addInterceptor(UnauthorizedInterceptor(tokenStore))
            .addInterceptor(logging)
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .build()

        val moshi = Moshi.Builder()
            .add(KotlinJsonAdapterFactory())
            .build()

        val retrofit = Retrofit.Builder()
            .baseUrl(BuildConfig.BASE_URL.trimEnd('/') + "/")
            .client(client)
            .addConverterFactory(MoshiConverterFactory.create(moshi))
            .build()

        api = retrofit.create(Api::class.java)
    }
}
