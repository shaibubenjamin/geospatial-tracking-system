package org.ehealth.eritas

import android.app.Application
import org.ehealth.eritas.core.net.ServiceLocator

class EritasApp : Application() {
    override fun onCreate() {
        super.onCreate()
        ServiceLocator.init(this)
    }
}
