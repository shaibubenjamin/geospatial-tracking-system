package org.ehealth.eritas.core.project

import android.content.Context

/**
 * Remembers which project (state + round) the user last selected, so every
 * data call can be scoped to it. Null means "use the server's active project".
 */
class ProjectStore(context: Context) {

    private val prefs = context.getSharedPreferences("eritas_project", Context.MODE_PRIVATE)

    var selectedProjectId: Int?
        get() = if (prefs.contains(KEY_PID)) prefs.getInt(KEY_PID, -1).takeIf { it >= 0 } else null
        set(value) = prefs.edit().apply {
            if (value == null) remove(KEY_PID) else putInt(KEY_PID, value)
        }.apply()

    var selectedProjectLabel: String?
        get() = prefs.getString(KEY_LABEL, null)
        set(value) = prefs.edit().putString(KEY_LABEL, value).apply()

    /** Forget the selected project - called on logout so the next user doesn't
     *  inherit the previous account's round (e.g. an admin's Kano selection
     *  carrying into a Sokoto-only LGA login and erroring until manually
     *  switched). A fresh login then falls back to the server's default project
     *  for that account. */
    fun clear() {
        prefs.edit().remove(KEY_PID).remove(KEY_LABEL).apply()
    }

    private companion object {
        const val KEY_PID = "selected_project_id"
        const val KEY_LABEL = "selected_project_label"
    }
}
