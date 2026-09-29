package dev.arco.orpheus

import org.json.JSONObject

/**
 * The phone <-> server protocol over one WebSocket; see docs/android-protocol.md.
 * Text frames are JSON messages, binary frames are 16-bit little-endian mono PCM:
 * the phone sends its microphone at 16 kHz, the server sends the reply at the rate given in "audio".
 */
object Protocol {
    /** [voice]: "male" or "female"; without it the server answers in its default voice.
     *  [enroll]: the phrase is one the owner reads to record his voice, not a question.
     *  [headset]: heard through the earbuds' microphone (true) or the phone's (false);
     *  [strict]: answer only the owner's voice (asked for in the earbuds, see Settings.headsetStrict). */
    fun start(
        followUp: Boolean, voice: String? = null, enroll: Boolean = false,
        headset: Boolean? = null, strict: Boolean = false, id: Int? = null,
    ) = JSONObject()
        .put("type", "start")
        .put("sample_rate", SAMPLE_RATE)
        .put("follow_up", followUp)
        .put("wake_word", "орфей")
        .apply { if (!voice.isNullOrBlank()) put("voice", voice) }
        .apply { if (enroll) put("enroll", true) }
        .apply { if (headset != null) put("headset", headset) }
        .apply { if (strict) put("strict", true) }
        .apply { if (id != null) put("id", id) }
        .toString()

    /** Forget the owner's recorded voice; the server answers with [ServerMessage.Enroll]. */
    fun enrollReset() = """{"type":"enroll_reset"}"""

    fun stop() = """{"type":"stop"}"""
    /** Enter or leave the "Личное" section; the server answers with [ServerMessage.Mode]. */
    fun mode(personal: Boolean) = JSONObject().put("type", "mode").put("personal", personal).toString()
    fun cancel() = """{"type":"cancel"}"""
    /** [personalKey] opens "Личное" on the server; it travels only inside the TLS connection. */
    fun hello(device: String, personalKey: String = "") = JSONObject()
        .put("type", "hello").put("device", device).put("version", 1)
        .apply { if (personalKey.isNotBlank()) put("personal_key", personalKey.trim()) }
        .toString()

    fun parse(text: String): ServerMessage {
        val o = try {
            JSONObject(text)
        } catch (e: Exception) {
            return ServerMessage.Unknown(text)
        }
        val id = if (o.has("id")) o.optInt("id") else null  // the phrase it is about; none from an older server
        return when (o.optString("type")) {
            "end_of_speech" -> ServerMessage.EndOfSpeech
            "transcript" -> ServerMessage.Transcript(o.optString("text"), id)
            "reply" -> ServerMessage.Reply(o.optString("text"), id)
            "audio" -> ServerMessage.AudioStart(o.optInt("sample_rate", 22_050), id)
            "audio_end" -> ServerMessage.AudioEnd(
                o.optBoolean("expect_reply", false), o.optBoolean("listen", true), o.optBoolean("pause", false), id,
                notOwner = o.optString("reason") == "not_owner",
            )
            "error" -> ServerMessage.Error(o.optString("message", "ошибка сервера"), id)
            "mode" -> ServerMessage.Mode(o.optBoolean("personal", false))
            "announce" -> ServerMessage.Announce(o.optString("text", ""), o.optInt("sample_rate", 22050))
            "announce_end" -> ServerMessage.AnnounceEnd
            "enroll" -> ServerMessage.Enroll(
                o.optInt("count", 0), o.optInt("needed", 5), o.optString("mode", ""),
                o.optString("error").takeIf { it.isNotBlank() },
            )
            else -> ServerMessage.Unknown(text)
        }
    }
}

sealed interface ServerMessage {
    data object EndOfSpeech : ServerMessage
    /** What the server heard. */
    data class Transcript(val text: String, val id: Int? = null) : ServerMessage
    /** Orpheus' answer as text (it may come in several pieces). */
    data class Reply(val text: String, val id: Int? = null) : ServerMessage
    /** Binary frames that follow are the spoken reply at this rate. */
    data class AudioStart(val sampleRate: Int, val id: Int? = null) : ServerMessage
    /** [listen]: false after «Стоп»/«Хватит» or a phrase with no words - back to waiting for «Орфей»;
     *  [pause]: «Орфей, не слушать» - not even the wake word until a button is pressed. */
    data class AudioEnd(
        val expectReply: Boolean, val listen: Boolean = true, val pause: Boolean = false, val id: Int? = null,
        /** "reason": "not_owner" - the voice was not taken for the owner's (strict): no answer to it. */
        val notOwner: Boolean = false,
    ) : ServerMessage
    data class Error(val message: String, val id: Int? = null) : ServerMessage
    /** Which section the server is in; it may change by voice or after a long silence too. */
    data class Mode(val personal: Boolean) : ServerMessage
    /** The owner's voice on the server: [count] phrases recorded of [needed]; [mode] "off" | "log" | "strict";
     *  [error] why the last phrase was not taken. Sent after "hello" and after each enrollment phrase. */
    data class Enroll(val count: Int, val needed: Int, val mode: String, val error: String? = null) : ServerMessage
    /** Said by the server by itself (a planner's reminder): binary frames at [sampleRate] follow, then [AnnounceEnd]. */
    data class Announce(val text: String, val sampleRate: Int) : ServerMessage
    data object AnnounceEnd : ServerMessage
    data class Unknown(val raw: String) : ServerMessage
}
