package org.ehealth.eritas.feature.project

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.ehealth.eritas.core.model.ProjectDto
import org.ehealth.eritas.core.net.ServiceLocator

/** A campaign label, e.g. "Sokoto — Round 5". */
fun projectLabel(p: ProjectDto): String {
    val state = p.stateName ?: p.name
    val round = p.roundNumber?.let { " — Round $it" } ?: ""
    return "$state$round" + if (p.isActive) " (active)" else ""
}

/**
 * Dialog listing every project (state + round) the app can scope to. Selecting
 * one persists the choice and reports it up so every screen re-scopes.
 */
@Composable
fun ProjectPickerDialog(
    onDismiss: () -> Unit,
    onSelect: (id: Int, label: String) -> Unit,
) {
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var projects by remember { mutableStateOf<List<ProjectDto>>(emptyList()) }

    LaunchedEffect(Unit) {
        projects = try {
            ServiceLocator.api.projects()
        } catch (e: Exception) {
            error = "Could not load campaigns: ${e.message ?: "network error"}"
            emptyList()
        }
        loading = false
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } },
        title = { Text("Select campaign") },
        text = {
            when {
                loading -> CircularProgressIndicator()
                error != null -> Text(error!!, color = MaterialTheme.colorScheme.error)
                else -> LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(projects) { p ->
                        Column(
                            Modifier
                                .fillMaxWidth()
                                .clickable {
                                    val label = projectLabel(p)
                                    ServiceLocator.projectStore.selectedProjectId = p.id
                                    ServiceLocator.projectStore.selectedProjectLabel = label
                                    onSelect(p.id, label)
                                }
                                .padding(vertical = 12.dp),
                        ) {
                            Text(projectLabel(p), style = MaterialTheme.typography.bodyLarge)
                            Text(p.name, style = MaterialTheme.typography.bodySmall)
                        }
                        HorizontalDivider()
                    }
                }
            }
        },
    )
}
