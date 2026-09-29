package dev.arco.orpheus

// The few app types the ui code uses, as they are in the app (AssistantMachine.kt, Bus.kt,
// Settings.kt, OrpheusService.kt), without their Android parts. Keep them in step.

enum class Phase { Off, Wake, Listening, Thinking, Speaking, FollowUp, Paused }

enum class Link { Off, Connecting, Online, NoServer, Denied }

data class Line(val fromUser: Boolean, val text: String, val at: Long = System.currentTimeMillis())

data class VoiceEnroll(
    val count: Int = 0,
    val needed: Int = 5,
    val mode: String = "",
    val error: String? = null,
    val recording: Boolean = false,
) {
    val ready get() = count >= needed
}

data class Settings(
    val serverUrl: String = "",
    val token: String = "",
    val followUp: Boolean = true,
    val followUpSeconds: Int = 6,
    val trigger: String = "touch",
    val wakeConfidence: Float = 0.75f,
    val earcons: Boolean = true,
    val headsetMic: Boolean = true,
    val headsetStrict: Boolean = true,
    val voice: String = "male",
    val personalKey: String = "",
)

object KeyVault {
    fun isValid(key: String) = key.length == 44
}

fun Phase.label() = when (this) {
    Phase.Off -> "Выключен"
    Phase.Wake -> "Скажи «Орфей»"
    Phase.Listening -> "Слушаю…"
    Phase.Thinking -> "Думаю…"
    Phase.Speaking -> "Отвечаю…"
    Phase.FollowUp -> "Слушаю продолжение…"
    Phase.Paused -> "Пауза: не слушаю"
}
