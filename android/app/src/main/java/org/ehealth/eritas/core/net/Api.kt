package org.ehealth.eritas.core.net

import okhttp3.ResponseBody
import org.ehealth.eritas.core.model.GeoSummary
import org.ehealth.eritas.core.model.LgaCoverage
import org.ehealth.eritas.core.model.LoginRequest
import org.ehealth.eritas.core.model.LoginResponse
import org.ehealth.eritas.core.model.NearResponse
import org.ehealth.eritas.core.model.OverviewDto
import org.ehealth.eritas.core.model.ProjectDto
import org.ehealth.eritas.core.model.TrendPoint
import org.ehealth.eritas.core.model.VersionInfo
import org.ehealth.eritas.core.model.WardCoverage
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

interface Api {

    /** Public, un-gated launch check. */
    @GET("/version")
    suspend fun version(): VersionInfo

    /** Public login; returns the JWT used as a Bearer token thereafter. */
    @POST("/api/auth/login")
    suspend fun login(@Body request: LoginRequest): LoginResponse

    // ── Gated app surface (/api/app/*) — requires a token + a current version ─

    @GET("/api/app/projects")
    suspend fun projects(): List<ProjectDto>

    @GET("/api/app/overview")
    suspend fun overview(@Query("project_id") projectId: Int?): OverviewDto

    @GET("/api/app/trends/daily")
    suspend fun trendsDaily(@Query("project_id") projectId: Int?): List<TrendPoint>

    @GET("/api/app/coverage/lga")
    suspend fun coverageLga(@Query("project_id") projectId: Int?): List<LgaCoverage>

    @GET("/api/app/coverage/ward")
    suspend fun coverageWard(
        @Query("lga") lga: String?,
        @Query("project_id") projectId: Int?,
    ): List<WardCoverage>

    /** Ward polygons + coverage as raw GeoJSON, fed straight to MapLibre. */
    @GET("/api/app/geo/wards")
    suspend fun wardsGeoJson(@Query("project_id") projectId: Int?): ResponseBody

    @GET("/api/app/geo/summary")
    suspend fun geoSummary(@Query("project_id") projectId: Int?): GeoSummary

    @GET("/api/app/near")
    suspend fun near(
        @Query("lat") lat: Double,
        @Query("lon") lon: Double,
        @Query("project_id") projectId: Int?,
    ): NearResponse
}
