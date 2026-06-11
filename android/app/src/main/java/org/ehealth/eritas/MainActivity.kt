package org.ehealth.eritas

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Flag
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.ui.Alignment
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.runtime.LaunchedEffect
import org.ehealth.eritas.core.auth.SessionManager
import org.ehealth.eritas.core.model.VersionInfo
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.feature.coverage.LgaCoverageScreen
import org.ehealth.eritas.feature.dashboard.DashboardScreen
import org.ehealth.eritas.feature.geo.GeographicScreen
import org.ehealth.eritas.feature.locate.MyAreaScreen
import org.ehealth.eritas.feature.login.LoginScreen
import org.ehealth.eritas.feature.quality.QualityScreen
import org.ehealth.eritas.feature.project.ProjectPickerDialog
import org.ehealth.eritas.feature.update.UpdateBanner
import org.ehealth.eritas.feature.update.UpdateGate
import org.ehealth.eritas.ui.EritasGreen
import org.ehealth.eritas.ui.EritasTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            EritasTheme {
              // A themed Surface backs every screen so login and the rest
              // respect dark/light mode (without it, screens fell through to
              // the white window background).
              Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                // The update gate is the outermost UI: a too-old install can
                // never reach login or the data screens.
                UpdateGate { optionalUpdate ->
                    var loggedIn by remember { mutableStateOf(ServiceLocator.tokenStore.isLoggedIn) }
                    // Auto-logout: when the server rejects our token (401), the
                    // networking layer trips SessionManager. Observe it and drop
                    // back to login instead of every screen showing "HTTP 401".
                    val sessionExpired = SessionManager.sessionExpired
                    LaunchedEffect(sessionExpired) {
                        if (sessionExpired) loggedIn = false
                    }
                    if (!loggedIn) {
                        // Surface the non-blocking "update available" banner on the
                        // login screen too (it used to appear only post-login). A
                        // too-old build never reaches here — UpdateGate shows the
                        // blocking wall first — so this is purely the optional case.
                        LoginScreen(optionalUpdate = optionalUpdate, onLoggedIn = {
                            SessionManager.reset()
                            loggedIn = true
                        })
                    } else {
                        MainScaffold(
                            optionalUpdate = optionalUpdate,
                            onLogout = {
                                ServiceLocator.tokenStore.clear()
                                SessionManager.reset()
                                loggedIn = false
                            },
                        )
                    }
                }
              }
            }
        }
    }
}

private enum class Tab(val label: String) {
    DASHBOARD("Dashboard"),       // native overview: KPIs + cumulative trend
    QUALITY("Quality"),           // native QC + team + daily-trend metrics
    COVERAGE("LGA Coverage"),     // native LGA → ward → settlement drill-down
    GEO("Geo"),                   // geo coverage cards + the Leaflet map
    FIELD_GUIDE("Guide"),         // native GPS: where am I + where to cover next
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MainScaffold(optionalUpdate: VersionInfo?, onLogout: () -> Unit) {
    var selectedTab by remember { mutableStateOf(Tab.DASHBOARD) }
    var projectId by remember { mutableStateOf(ServiceLocator.projectStore.selectedProjectId) }
    var projectLabel by remember { mutableStateOf(ServiceLocator.projectStore.selectedProjectLabel) }
    var showPicker by remember { mutableStateOf(false) }
    var bannerDismissed by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    // Set when a map pin is tapped on the Coverage tab — switches to the Geo tab
    // focused on that LGA, and (for a settlement pin) zoomed onto the settlement.
    var mapFocusLga by remember { mutableStateOf<String?>(null) }
    var mapFocusSettlement by remember { mutableStateOf<String?>(null) }

    // Campaign switching is allowed ONLY on the Dashboard. Changing the project
    // updates `projectId`, which every tab reads, so they all reload with the
    // new campaign when next shown.
    val canSwitch = selectedTab == Tab.DASHBOARD

    Scaffold(
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = EritasGreen,
                    titleContentColor = Color.White,
                    navigationIconContentColor = Color.White,
                    actionIconContentColor = Color.White,
                ),
                navigationIcon = {
                    Image(
                        painter = painterResource(R.drawable.ic_launcher),
                        contentDescription = "ERITAS",
                        modifier = Modifier
                            .padding(start = 12.dp)
                            .size(34.dp)
                            .clip(RoundedCornerShape(9.dp)),
                    )
                },
                title = {
                    // Two-line title: the current SECTION/tab name is the bold,
                    // prominent line; the active campaign sits beneath it. On the
                    // Dashboard the campaign line is the switcher — tap it (or the
                    // swap icon) to change state/round.
                    Column {
                        Text(
                            selectedTab.label,
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.Bold,
                            color = Color.White,
                        )
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = if (canSwitch) Modifier.clickable { showPicker = true } else Modifier,
                        ) {
                            Text(
                                projectLabel ?: "Active campaign",
                                style = MaterialTheme.typography.labelMedium,
                                color = Color.White.copy(alpha = 0.85f),
                            )
                            if (canSwitch) {
                                Icon(Icons.Filled.ArrowDropDown, contentDescription = "Switch campaign")
                            }
                        }
                    }
                },
                actions = {
                    if (canSwitch) {
                        IconButton(onClick = { showPicker = true }) {
                            Icon(Icons.Filled.SwapHoriz, contentDescription = "Switch campaign")
                        }
                    }
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Filled.MoreVert, contentDescription = "More")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        DropdownMenuItem(
                            text = { Text("Log out") },
                            onClick = { menuOpen = false; onLogout() },
                        )
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar {
                NavigationBarItem(
                    selected = selectedTab == Tab.DASHBOARD,
                    onClick = { selectedTab = Tab.DASHBOARD },
                    icon = { Icon(Icons.Filled.Dashboard, contentDescription = null) },
                    label = { Text(Tab.DASHBOARD.label) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.QUALITY,
                    onClick = { selectedTab = Tab.QUALITY },
                    icon = { Icon(Icons.Filled.Flag, contentDescription = null) },
                    label = { Text(Tab.QUALITY.label) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.COVERAGE,
                    onClick = { selectedTab = Tab.COVERAGE },
                    icon = { Icon(Icons.Filled.BarChart, contentDescription = null) },
                    label = { Text(Tab.COVERAGE.label, maxLines = 1) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.GEO,
                    onClick = { selectedTab = Tab.GEO },
                    icon = { Icon(Icons.Filled.Map, contentDescription = null) },
                    label = { Text(Tab.GEO.label) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.FIELD_GUIDE,
                    onClick = { selectedTab = Tab.FIELD_GUIDE },
                    icon = { Icon(Icons.Filled.MyLocation, contentDescription = null) },
                    label = { Text(Tab.FIELD_GUIDE.label) },
                )
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (optionalUpdate != null && !bannerDismissed) {
                UpdateBanner(optionalUpdate) { bannerDismissed = true }
            }
            when (selectedTab) {
                // Native dashboard — KPIs + cumulative trend (real Material 3
                // layout, not the desktop /mda squeezed into a WebView).
                Tab.DASHBOARD -> DashboardScreen(projectId)
                // Native LGA → ward coverage drill-down. A row's map pin jumps to
                // the Geo tab focused on that LGA.
                Tab.COVERAGE -> LgaCoverageScreen(
                    projectId = projectId,
                    onOpenMap = { lga, settlement ->
                        mapFocusLga = lga; mapFocusSettlement = settlement; selectedTab = Tab.GEO
                    },
                )
                // Native QC + team + daily-trend metrics.
                Tab.QUALITY -> QualityScreen(projectId)
                // Geographic — coverage % cards over the Leaflet map. Honour a
                // pending LGA focus from a Coverage row's map pin.
                Tab.GEO -> GeographicScreen(projectId, focusLga = mapFocusLga, focusSettlement = mapFocusSettlement)
                Tab.FIELD_GUIDE -> MyAreaScreen(projectId)
            }
        }
    }

    if (showPicker) {
        ProjectPickerDialog(
            onDismiss = { showPicker = false },
            onSelect = { id, label ->
                projectId = id
                projectLabel = label
                showPicker = false
            },
        )
    }
}
