package dev.arco.orpheus

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings as AndroidSettings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import dev.arco.orpheus.ui.Fonts
import dev.arco.orpheus.ui.MainScreen
import dev.arco.orpheus.ui.OrpheusTheme
import dev.arco.orpheus.ui.Screens
import dev.arco.orpheus.ui.SettingsScreen
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private var backgroundAllowed by mutableStateOf(true)
    /** Orpheus is the phone's digital assistant (the buds' touch and hold calls it). */
    private var isAssistant by mutableStateOf(false)

    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.RECORD_AUDIO] == true) {
            OrpheusService.start(this)
        } else {
            Bus.problem.value = "Без микрофона Орфей не услышит"
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val fonts = Fonts(
            display = FontFamily(Font(R.font.unbounded_500, FontWeight.Medium), Font(R.font.unbounded_700, FontWeight.Bold)),
            body = FontFamily(Font(R.font.manrope_400, FontWeight.Normal), Font(R.font.manrope_600, FontWeight.SemiBold)),
        )
        // "Remove animations" in the system settings sets the animator scale to 0
        val reducedMotion = AndroidSettings.Global.getFloat(contentResolver, AndroidSettings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
        setContent {
            OrpheusTheme(fonts, reducedMotion) {
                var inSettings by androidx.compose.runtime.remember { mutableStateOf(false) }
                val scope = rememberCoroutineScope()
                val phase by Bus.phase.collectAsStateWithLifecycle()
                val link by Bus.link.collectAsStateWithLifecycle()
                val level by Bus.level.collectAsStateWithLifecycle()
                val lines by Bus.lines.collectAsStateWithLifecycle()
                val problem by Bus.problem.collectAsStateWithLifecycle()
                val enroll by Bus.enroll.collectAsStateWithLifecycle()
                val personal by Bus.personal.collectAsStateWithLifecycle()
                val settings by settingsFlow().collectAsStateWithLifecycle(null)

                BackHandler(enabled = inSettings) { inSettings = false }
                Screens(
                    inSettings && settings != null,
                    main = {
                        MainScreen(
                            phase, link, level, lines, problem, backgroundAllowed,
                            onToggle = { if (phase == Phase.Off) enable() else OrpheusService.stop(this) },
                            onTalk = { OrpheusService.talk(this) },
                            onSettings = { inSettings = true },
                            onAllowBackground = ::askBackground,
                            personal = personal,
                            onPersonal = { OrpheusService.personal(this, !personal) },
                            onClear = { Bus.clear() },
                            touch = settings?.trigger != "word",
                        )
                    },
                    settings = {
                        settings?.let { current ->
                            SettingsScreen(
                                current,
                                onSave = {
                                    scope.launch { saveSettings(it) }
                                    inSettings = false
                                },
                                onBack = { inSettings = false },
                                enroll = enroll,
                                phase = phase,
                                level = level,
                                onEnrollPhrase = { OrpheusService.enrollPhrase(this) },
                                onEnrollReset = { OrpheusService.enrollReset(this) },
                                isAssistant = isAssistant,
                                // the "Digital assistant app" page; the role itself can only be given by the owner there
                                onAssistantSettings = { startActivity(Intent(AndroidSettings.ACTION_VOICE_INPUT_SETTINGS)) },
                            )
                        }
                    },
                )
            }
        }
    }

    override fun onResume() {
        super.onResume()
        backgroundAllowed = getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)
        isAssistant = getSystemService(android.app.role.RoleManager::class.java)
            .isRoleHeld(android.app.role.RoleManager.ROLE_ASSISTANT)
    }

    private fun enable() {
        Bus.problem.value = null
        val needed = buildList {
            add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
        }.filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }
        if (needed.isEmpty()) OrpheusService.start(this) else permissions.launch(needed.toTypedArray())
    }

    /** Without this some phones kill a background service after a while, microphone or not. */
    @SuppressLint("BatteryLife")
    private fun askBackground() {
        startActivity(
            Intent(AndroidSettings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName"))
        )
    }
}
