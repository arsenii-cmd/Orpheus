package dev.arco.orpheus

import org.json.JSONObject

/**
 * The Vosk grammar: the wake word plus look-alikes and everyday words. With the wake word alone
 * Vosk forces any speech into it ("офис", "арфа", "Морфей" all came out as "орфей");
 * with neighbours to choose from, only the real word wins.
 */
val WAKE_GRAMMAR: String = run {
    val distractors = """
        офис арфа арфу морфей морфий орфографию фея фей эй хей окей орех оффер афера сфера
        верфи трофей корифей еврей ферзь форт фото орган ордер орёл ореол алло привет пока
        да нет как где что это сегодня погода время дела хорошо плохо ну вот так там тут
        он она они мы вы я ты день ночь утро вечер может будет было есть надо можно нельзя
        сейчас потом завтра вчера очень много мало работа дом машина телефон музыка включи
        выключи скажи слушай открой позвони напомни запиши играет пришёл открыт ночью
    """.trim().split(Regex("\\s+"))
    val words = listOf(WAKE_WORD) + distractors + "[unk]"
    words.joinToString(",", "[", "]") { "\"$it\"" }
}

const val WAKE_WORD = "орфей"

/**
 * Picks the wake word out of a Vosk final result (with word timings).
 * Final results rather than partial ones: partials jump to the wake word on "Морфей" and similar.
 * Returns the sample (counted from the recognizer's start) where the wake word ends, so that
 * whatever was said right after it can be taken from the ring buffer — nothing is lost when the
 * user says "Орфей, какая погода" in one breath.
 */
/**
 * Where in the ring buffer the wake word ends. Vosk times its words by the audio fed to it, but whether
 * its reset starts that count over is not to be relied on (27.09: counted from the wrong start, the phrase
 * after every «Орфей» but the first was never sent). Both readings are tried; the latest that lies in the
 * audio actually kept wins - the wake word has only just ended. Null: neither does.
 */
fun wakeEndInRing(end: Long, fedAll: Long, fedSinceReset: Long, ringTotal: Long, kept: Long): Long? =
    listOf(ringTotal - (fedAll - end), ringTotal - (fedSinceReset - end))
        .filter { it > ringTotal - kept && it <= ringTotal }
        .maxOrNull()

fun findWakeWord(resultJson: String, minConfidence: Double): Long? {
    val words = try {
        JSONObject(resultJson).optJSONArray("result")
    } catch (e: Exception) {
        null
    } ?: return null
    for (i in 0 until words.length()) {
        val w = words.getJSONObject(i)
        if (w.optString("word") == WAKE_WORD && w.optDouble("conf", 0.0) >= minConfidence) {
            return (w.optDouble("end", 0.0) * SAMPLE_RATE).toLong()
        }
    }
    return null
}
