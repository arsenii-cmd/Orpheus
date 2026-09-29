package dev.arco.orpheus

import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sin
import kotlin.math.sqrt

const val SAMPLE_RATE = 16_000

/** Frame loudness in dBFS: 0 is full scale, silence is around -90. */
fun levelDb(frame: ShortArray, count: Int = frame.size): Double {
    if (count == 0) return -96.0
    var sum = 0.0
    for (i in 0 until count) {
        val s = frame[i] / 32768.0
        sum += s * s
    }
    return 20 * log10(max(sqrt(sum / count), 1e-5))
}

/**
 * Loudness without the rumble under 100 Hz (2nd-order Butterworth high-pass). A fan or a humming
 * desk is 99 % of a quiet room's sound there and none of a voice's: on 28.09 it kept the room only
 * 4-7 dB under a voice read from a metre away, under the VAD's 9 dB margin, so no phrase ever
 * "started" and each one waited out the 6 s limit; without it the same voice stood 13-18 dB out.
 * Only the measure: the audio sent to the server is untouched. Keeps its state between frames, so
 * one instance per stream of audio.
 */
class Rumble(cutoffHz: Double = 100.0, rate: Int = SAMPLE_RATE) {
    private val b0: Double
    private val b1: Double
    private val b2: Double
    private val a1: Double
    private val a2: Double
    private var x1 = 0.0
    private var x2 = 0.0
    private var y1 = 0.0
    private var y2 = 0.0

    init {
        val w = 2 * PI * cutoffHz / rate
        val c = cos(w)
        val alpha = sin(w) / sqrt(2.0)
        val a0 = 1 + alpha
        b0 = (1 + c) / 2 / a0
        b1 = -(1 + c) / a0
        b2 = b0
        a1 = -2 * c / a0
        a2 = (1 - alpha) / a0
    }

    /** Like [levelDb], of the frame with the rumble taken out. */
    fun levelDb(frame: ShortArray, count: Int = frame.size): Double {
        if (count == 0) return -96.0
        var sum = 0.0
        for (i in 0 until count) {
            val x = frame[i] / 32768.0
            val y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1; x1 = x; y2 = y1; y1 = y
            sum += y * y
        }
        return 20 * log10(max(sqrt(sum / count), 1e-5))
    }
}

/**
 * Speech start and end by loudness over the background level. Crude compared with the server's
 * VAD, but it only has to notice "someone is talking" and "they stopped", and costs nothing.
 *
 * Levels are measured without the rumble ([Rumble]). The background is a low percentile of the last 8 s of frame levels, fed with every frame the
 * microphone gives ([observe]) — not only while listening. An average that creeps up on loud
 * frames would take a long phrase for background, and then the next phrase never "starts".
 */
class EnergyVad(
    private val marginDb: Double = 9.0,
    private val minSpeechDb: Double = -60.0,  // -55 missed a quiet voice after the wake word (27.09)
    private val startMs: Int = 150,
    private val silenceMs: Int = 1_000,
    private val historyFrames: Int = 160,  // 8 s of 50 ms frames: longer than any phrase without a pause
) {
    enum class Change { Started, Ended }

    private val rumble = Rumble()
    private val history = ArrayDeque<Double>()
    private var voicedMs = 0
    private var quietMs = 0
    var speaking = false
        private set

    /** The background level: what the room sounds like between words. */
    val floorDb: Double
        get() = if (history.isEmpty()) -70.0 else history.sorted()[history.size / 20]

    fun reset() {
        voicedMs = 0
        quietMs = 0
        speaking = false
    }

    /** Every frame goes here, listening or not, so the background is always current. */
    fun observe(db: Double) {
        history.addLast(db)
        if (history.size > historyFrames) history.removeFirst()
    }

    fun process(frame: ShortArray, count: Int = frame.size): Change? {
        val db = rumble.levelDb(frame, count)
        val floor = floorDb  // before this frame joins the history
        observe(db)
        val ms = count * 1000 / SAMPLE_RATE
        val voiced = db > floor + marginDb && db > minSpeechDb
        if (voiced) {
            voicedMs += ms
            quietMs = 0
        } else {
            quietMs += ms
            if (!speaking) voicedMs = 0
        }
        if (!speaking && voicedMs >= startMs) {
            speaking = true
            return Change.Started
        }
        if (speaking && quietMs >= silenceMs) {
            speaking = false
            voicedMs = 0
            return Change.Ended
        }
        return null
    }
}

/** The last few seconds of microphone audio, addressable by absolute sample number. */
/** How much microphone audio is kept: enough for a phrase said right after the wake word. */
const val RING_SAMPLES = SAMPLE_RATE * 10L

class AudioRing(private val capacity: Int = RING_SAMPLES.toInt()) {
    private val data = ShortArray(capacity)
    /** Samples written since creation. */
    var total = 0L
        private set

    fun write(frame: ShortArray, count: Int = frame.size) {
        for (i in 0 until count) {
            data[((total + i) % capacity).toInt()] = frame[i]
        }
        total += count
    }

    /** Samples from absolute position [from] to now; clamped to what is still kept. */
    fun since(from: Long): ShortArray {
        val start = from.coerceIn(max(0L, total - capacity), total)
        val out = ShortArray((total - start).toInt())
        for (i in out.indices) out[i] = data[((start + i) % capacity).toInt()]
        return out
    }

    fun last(samples: Int): ShortArray = since(total - samples)
}

fun ShortArray.toLittleEndian(count: Int = size): ByteArray {
    val out = ByteArray(count * 2)
    for (i in 0 until count) {
        val v = this[i].toInt()
        out[2 * i] = v.toByte()
        out[2 * i + 1] = (v shr 8).toByte()
    }
    return out
}

fun ByteArray.toShorts(): ShortArray {
    val out = ShortArray(size / 2)
    for (i in out.indices) {
        out[i] = ((this[2 * i].toInt() and 0xff) or (this[2 * i + 1].toInt() shl 8)).toShort()
    }
    return out
}
