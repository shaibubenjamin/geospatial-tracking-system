package org.ehealth.eritas

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Map
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.MyLocation
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import org.ehealth.eritas.core.model.VersionInfo
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.feature.coverage.LgaCoverageScreen
import org.ehealth.eritas.feature.dashboard.DashboardScreen
import org.ehealth.eritas.feature.locate.MyAreaScreen
import org.ehealth.eritas.feature.login.LoginScreen
import org.ehealth.eritas.feature.map.CoverageMapScreen
import org.ehealth.eritas.feature.project.ProjectPickerDialog
import org.ehealth.eritas.feature.update.UpdateBanner
import org.ehealth.eritas.feature.update.UpdateGate
import org.ehealth.eritas.ui.EritasTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            EritasTheme {
                // The update gate is the outermost UI: a too-old install can
                // never reach login or the data screens.
                UpdateGate { optionalUpdate ->
                    var loggedIn by remember { mutableStateOf(ServiceLocator.tokenStore.isLoggedIn) }
                    if (!loggedIn) {
                        LoginScreen(onLoggedIn = { loggedIn = true })
                    } else {
                        MainScaffold(
                            optionalUpdate = optionalUpdate,
                            onLogout = {
                                ServiceLocator.tokenStore.clear()
                                loggedIn = false
                            },
                        )
                    }
                }
            }
        }
    }
}

private enum class Tab(val label: String) {
    DASHBOARD("Dashboard"),
    COVERAGE("Coverage"),
    MAP("Map"),
    MY_AREA("My Area"),
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

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(projectLabel ?: "Active campaign") },
                actions = {
                    IconButton(onClick = { showPicker = true }) {
                        Icon(Icons.Filled.Layers, contentDescription = "Select campaign")
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
                    selected = selectedTab == Tab.COVERAGE,
                    onClick = { selectedTab = Tab.COVERAGE },
                    icon = { Icon(Icons.Filled.BarChart, contentDescription = null) },
                    label = { Text(Tab.COVERAGE.label) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.MAP,
                    onClick = { selectedTab = Tab.MAP },
                    icon = { Icon(Icons.Filled.Map, contentDescription = null) },
                    label = { Text(Tab.MAP.label) },
                )
                NavigationBarItem(
                    selected = selectedTab == Tab.MY_AREA,
                    onClick = { selectedTab = Tab.MY_AREA },
                    icon = { Icon(Icons.Filled.MyLocation, contentDescription = null) },
                    label = { Text(Tab.MY_AREA.label) },
                )
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (optionalUpdate != null && !bannerDismissed) {
                UpdateBanner(optionalUpdate) { bannerDismissed = true }
            }
            when (selectedTab) {
                Tab.DASHBOARD -> DashboardScreen(projectId)
                Tab.COVERAGE -> LgaCoverageScreen(projectId)
                Tab.MAP -> CoverageMapScreen(projectId)
                Tab.MY_AREA -> MyAreaScreen(projectId)
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
