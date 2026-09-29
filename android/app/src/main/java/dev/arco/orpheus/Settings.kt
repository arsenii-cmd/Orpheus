package dev.arco.orpheus

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

data class Settings(
    /** Empty means the built-in address and token (BuildConfig), see [effectiveUrl]. */
    val serverUrl: String = "",
    val token: String = "",
    /** Keep listening for a few seconds after the reply, without the wake word. */
    val followUp: Boolean = true,
    val followUpSeconds: Int = 6,
    /** How Orpheus is called: "touch" — the buds' touch and hold (Orpheus as the digital assistant), the
     *  microphone open for one conversation only; "word" — «Орфей», the microphone always open. */
    val trigger: String = "touch",
    /** Vosk confidence the wake word needs: higher means fewer false alarms, more misses. */
    val wakeConfidence: Float = 0.75f,
    val earcons: Boolean = true,
    /** Listen through the earbuds' microphone while they are connected (HeadsetMic). */
    val headsetMic: Boolean = true,
    /** In the earbuds, only the owner's voice gets an answer; through the phone's microphone, everyone's. */
    val headsetStrict: Boolean = true,
    /** The server's voice for the replies: "male" (Piper ruslan) or "female" (Piper irina). */
    val voice: String = "male",
    /** The key of "Личное" (base64, 32 bytes); on disk only wrapped by the Android Keystore (KeyVault). */
    val personalKey: String = "",
)

val Settings.effectiveUrl get() = serverUrl.ifBlank { BuildConfig.DEFAULT_SERVER }
val Settings.effectiveToken get() = token.ifBlank { BuildConfig.DEFAULT_TOKEN }

private val Context.store by preferencesDataStore("settings")

private object Keys {
    val url = stringPreferencesKey("server_url")
    val token = stringPreferencesKey("token")
    val followUp = booleanPreferencesKey("follow_up")
    val followUpSeconds = intPreferencesKey("follow_up_seconds")
    val wakeConfidence = floatPreferencesKey("wake_confidence")
    val earcons = booleanPreferencesKey("earcons")
    val trigger = stringPreferencesKey("trigger")
    val headsetMic = booleanPreferencesKey("headset_mic")
    val headsetStrict = booleanPreferencesKey("headset_strict")
    val voice = stringPreferencesKey("voice")
    val personalKey = stringPreferencesKey("personal_key_wrapped")
}

fun Context.settingsFlow(): Flow<Settings> = store.data.map { p ->
    val d = Settings()
    Settings(
        serverUrl = p[Keys.url] ?: d.serverUrl,
        token = p[Keys.token] ?: d.token,
        followUp = p[Keys.followUp] ?: d.followUp,
        followUpSeconds = p[Keys.followUpSeconds] ?: d.followUpSeconds,
        wakeConfidence = p[Keys.wakeConfidence] ?: d.wakeConfidence,
        earcons = p[Keys.earcons] ?: d.earcons,
        trigger = p[Keys.trigger] ?: d.trigger,
        headsetMic = p[Keys.headsetMic] ?: d.headsetMic,
        headsetStrict = p[Keys.headsetStrict] ?: d.headsetStrict,
        voice = p[Keys.voice] ?: d.voice,
        personalKey = KeyVault.unwrap(p[Keys.personalKey] ?: ""),
    )
}

suspend fun Context.saveSettings(s: Settings) {
    store.edit { p ->
        p[Keys.url] = s.serverUrl.trim()
        p[Keys.token] = s.token.trim()
        p[Keys.followUp] = s.followUp
        p[Keys.followUpSeconds] = s.followUpSeconds
        p[Keys.wakeConfidence] = s.wakeConfidence
        p[Keys.earcons] = s.earcons
        p[Keys.trigger] = s.trigger
        p[Keys.headsetMic] = s.headsetMic
        p[Keys.headsetStrict] = s.headsetStrict
        p[Keys.voice] = s.voice
        p[Keys.personalKey] = KeyVault.wrap(s.personalKey.trim())
    }
}
