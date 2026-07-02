package org.ehealth.eritas.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// ERITAS palette - green primary to match the dashboard's coverage theme.
val EritasGreen = Color(0xFF0E7C4A)
val EritasGreenDark = Color(0xFF0A5C37)
val CoverageGood = Color(0xFF2E7D32)
val CoverageMid = Color(0xFFF9A825)
val CoverageLow = Color(0xFFC62828)

private val LightColors = lightColorScheme(
    primary = EritasGreen,
    secondary = EritasGreenDark,
)

private val DarkColors = darkColorScheme(
    primary = EritasGreen,
    secondary = EritasGreenDark,
)

@Composable
fun EritasTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) DarkColors else LightColors,
        content = content,
    )
}
