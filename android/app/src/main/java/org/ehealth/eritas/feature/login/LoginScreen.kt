package org.ehealth.eritas.feature.login

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import org.ehealth.eritas.R
import org.ehealth.eritas.core.model.LoginRequest
import org.ehealth.eritas.core.model.VersionInfo
import org.ehealth.eritas.core.net.ServiceLocator
import org.ehealth.eritas.feature.update.UpdateBanner

@Composable
fun LoginScreen(optionalUpdate: VersionInfo? = null, onLoggedIn: () -> Unit) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var bannerDismissed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()

    Column(Modifier.fillMaxSize()) {
      // Optional "update available" banner, pinned above the form. A too-old
      // build never reaches login (UpdateGate shows the blocking wall first),
      // so this only ever shows the soft, dismissible update prompt.
      if (optionalUpdate != null && !bannerDismissed) {
          UpdateBanner(optionalUpdate) { bannerDismissed = true }
      }
      Column(
        Modifier.fillMaxWidth().weight(1f).padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Image(
            painter = painterResource(R.drawable.ic_launcher),
            contentDescription = null,
            modifier = Modifier.size(84.dp).clip(RoundedCornerShape(20.dp)),
        )
        Spacer(Modifier.height(16.dp))
        Text(
            "ERITAS MDA Coverage",
            style = MaterialTheme.typography.headlineSmall,
            fontWeight = FontWeight.Bold,
            color = MaterialTheme.colorScheme.primary,
            textAlign = TextAlign.Center,
        )
        Text(
            "Field coverage monitoring",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(28.dp))

        Card(Modifier.fillMaxWidth(), shape = RoundedCornerShape(20.dp)) {
            Column(Modifier.padding(20.dp)) {
                OutlinedTextField(
                    value = username,
                    onValueChange = { username = it; error = null },
                    label = { Text("Username") },
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it; error = null },
                    label = { Text("Password") },
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth(),
                )
                if (error != null) {
                    Spacer(Modifier.height(12.dp))
                    Text(error!!, color = MaterialTheme.colorScheme.error)
                }
                Spacer(Modifier.height(20.dp))
                Button(
                    onClick = {
                        if (username.isBlank() || password.isBlank()) {
                            error = "Enter your username and password"
                            return@Button
                        }
                        loading = true
                        error = null
                        scope.launch {
                            try {
                                val resp = ServiceLocator.api.login(
                                    LoginRequest(username.trim(), password)
                                )
                                ServiceLocator.tokenStore.token = resp.accessToken
                                ServiceLocator.tokenStore.username = resp.username
                                onLoggedIn()
                            } catch (e: Exception) {
                                error = "Sign-in failed. Check your details and connection."
                            } finally {
                                loading = false
                            }
                        }
                    },
                    enabled = !loading,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().height(52.dp),
                ) {
                    if (loading) CircularProgressIndicator(Modifier.size(22.dp)) else Text("Sign in")
                }
            }
        }
      }
    }
}
