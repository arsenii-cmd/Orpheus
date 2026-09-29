package dev.arco.orpheus

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update

enum class Link { Off, Connecting, Online, NoServer, Denied }

/** [personal]: said in "Личное" — gone from the screen as soon as "Личное" closes. */
data class Line(val fromUser: Boolean, val text: String, val at: Long = System.currentTimeMillis(), val personal: Boolean = false)

/** The owner's voice as the server knows it; [recording]: the next phrases are read to record it. */
data class VoiceEnroll(
    val count: Int = 0,
    val needed: Int = 5,
    val mode: String = "",
    val error: String? = null,
    val recording: Boolean = false,
) {
    val ready get() = count >= needed
}

/** What the service shows to the UI: it lives in the same process, so plain state flows will do. */
object Bus {
    val phase = MutableStateFlow(Phase.Off)
    val link = MutableStateFlow(Link.Off)
    /** Microphone level 0..1 while listening, for the orb. */
    val level = MutableStateFlow(0f)
    val lines = MutableStateFlow<List<Line>>(emptyList())
    /** A one-off problem worth showing ("no model", "server unreachable"). */
    val problem = MutableStateFlow<String?>(null)
    /** The "Личное" section, as the server reports it. */
    val personal = MutableStateFlow(false)
    /** The owner's voice recording (settings → «Мой голос»). */
    val enroll = MutableStateFlow(VoiceEnroll())

    fun say(fromUser: Boolean, text: String) {
        if (text.isBlank()) return
        lines.update { (it + Line(fromUser, text.trim(), personal = personal.value)).takeLast(100) }
    }

    /** Streamed reply text is appended to the last line of Orpheus instead of starting a new one. */
    fun append(text: String) {
        lines.update { list ->
            val last = list.lastOrNull()
            if (last != null && !last.fromUser && System.currentTimeMillis() - last.at < 60_000) {
                list.dropLast(1) + last.copy(text = last.text + text, personal = last.personal || personal.value)
            } else {
                (list + Line(false, text, personal = personal.value)).takeLast(100)
            }
        }
    }

    /** "Личное" closed (by voice, the button, silence or Orpheus off): what was said there leaves the screen. */
    fun forgetPersonal() {
        lines.update { list -> list.filterNot { it.personal } }
    }

    /** «Очистить»: the conversation on the screen only (the server keeps its memory, and "Личное" its own). */
    fun clear() {
        lines.value = emptyList()
    }
}
