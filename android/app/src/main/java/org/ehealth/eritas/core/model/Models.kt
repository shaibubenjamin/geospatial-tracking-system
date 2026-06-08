package org.ehealth.eritas.core.model

import com.squareup.moshi.Json

/** Response of GET /version — drives the launch-time update check. */
data class VersionInfo(
    val min: Int,
    val latest: Int,
    @Json(name = "latest_name") val latestName: String,
    @Json(name = "update_url") val updateUrl: String,
)

data class LoginRequest(
    val username: String,
    val password: String,
)

data class LoginResponse(
    @Json(name = "access_token") val accessToken: String,
    val username: String,
    @Json(name = "is_admin") val isAdmin: Boolean = false,
    @Json(name = "is_superadmin") val isSuperadmin: Boolean = false,
)

/** A campaign — one state + round. Source for the project selector. */
data class ProjectDto(
    val id: Int,
    val name: String,
    @Json(name = "state_name") val stateName: String?,
    @Json(name = "round_number") val roundNumber: Int?,
    @Json(name = "is_active") val isActive: Boolean = false,
)

/** Subset of GET /api/app/overview we surface on the dashboard. Moshi ignores
 *  the many other keys the endpoint returns. */
data class OverviewDto(
    @Json(name = "total_forms") val totalForms: Int = 0,
    @Json(name = "total_treated") val totalTreated: Int = 0,
    @Json(name = "coverage_pct") val coveragePct: Double = 0.0,
    @Json(name = "teams_active") val teamsActive: Int = 0,
    @Json(name = "lgas_covered") val lgasCovered: Int = 0,
    @Json(name = "days_active") val daysActive: Int = 0,
    @Json(name = "current_campaign_day") val currentCampaignDay: Int? = null,
    @Json(name = "planned_duration_days") val plannedDurationDays: Int? = null,
    @Json(name = "error_rate_pct") val errorRatePct: Double = 0.0,
    @Json(name = "total_qc_flags") val totalQcFlags: Int = 0,
    @Json(name = "refusals") val refusals: Int = 0,
)

/** Response of GET /api/app/near — the core field-coverage aid. */
data class NearResponse(
    @Json(name = "project_id") val projectId: Int,
    val current: NearCurrent?,
    @Json(name = "nearest_uncovered") val nearestUncovered: NearTarget?,
)

data class NearCurrent(
    @Json(name = "settlement_name") val settlementName: String?,
    @Json(name = "ward_name") val wardName: String?,
    @Json(name = "lga_name") val lgaName: String?,
    @Json(name = "completeness_pct") val completenessPct: Double,
    @Json(name = "is_covered") val isCovered: Boolean,
    @Json(name = "point_count") val pointCount: Int,
)

data class NearTarget(
    @Json(name = "settlement_name") val settlementName: String?,
    @Json(name = "ward_name") val wardName: String?,
    @Json(name = "lga_name") val lgaName: String?,
    @Json(name = "completeness_pct") val completenessPct: Double,
    @Json(name = "distance_m") val distanceM: Double,
    val lat: Double?,
    val lon: Double?,
)
