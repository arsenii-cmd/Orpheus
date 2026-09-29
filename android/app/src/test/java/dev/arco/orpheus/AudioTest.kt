package dev.arco.orpheus

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin
import kotlin.random.Random

class AudioTest {
    private val frame = SAMPLE_RATE / 20

    private fun noise(amplitude: Int) = ShortArray(frame) { (Random.nextInt(-amplitude, amplitude + 1)).toShort() }
    private fun voice(amplitude: Int, offset: Int) = ShortArray(frame) { (sin(2 * PI * 220 * (offset + it) / SAMPLE_RATE) * amplitude).toInt().toShort() }

    @Test fun vadFindsSpeechOverNoiseAndItsEnd() {
        val vad = EnergyVad(silenceMs = 1_000)
        val changes = mutableListOf<Pair<Int, EnergyVad.Change>>()
        var t = 0
        fun feed(f: ShortArray) { vad.process(f)?.let { changes += t to it }; t += 50 }
        repeat(40) { feed(noise(100)) }            // 2 s of room noise
        repeat(30) { feed(voice(6000, it * frame)) } // 1.5 s of voice
        repeat(40) { feed(noise(100)) }            // 2 s of noise again
        assertEquals(2, changes.size)
        assertEquals(EnergyVad.Change.Started, changes[0].second)
        assertEquals(2_100, changes[0].first)        // after 150 ms of voice
        assertEquals(EnergyVad.Change.Ended, changes[1].second)
        // voice ends at 3.5 s, then 1 s of quiet; 50 ms more: the rumble filter rings in the frame after
        // a tone cut off mid-wave (a voice fades out, a test tone does not)
        assertEquals(4_500, changes[1].first)
    }

    @Test fun aLongPhraseDoesNotRaiseTheBackgroundAndTheNextOneStillStarts() {
        val vad = EnergyVad()
        repeat(40) { vad.observe(levelDb(noise(100))) }        // the room, heard while waiting
        var starts = 0
        repeat(3) { phrase ->
            repeat(60) { if (vad.process(voice(5000, it * frame)) == EnergyVad.Change.Started) starts++ }  // 3 s of talk
            repeat(30) { vad.process(noise(100)) }                                                            // 1.5 s pause
        }
        assertEquals(3, starts)
    }

    @Test fun aQuietVoiceOverALoudHumStillStarts() {
        // a hum under 100 Hz kept a voice from a metre away only 4-7 dB over the room
        fun hum(offset: Int) = ShortArray(frame) { (sin(2 * PI * 30 * (offset + it) / SAMPLE_RATE) * 800).toInt().toShort() }
        fun mix(a: ShortArray, b: ShortArray) = ShortArray(frame) { (a[it] + b[it]).toShort() }
        // measured as it is, the hum drowns the voice: under the VAD's 9 dB margin
        assertTrue(levelDb(mix(hum(0), voice(400, 0))) - levelDb(hum(0)) < 9)
        val vad = EnergyVad()
        val room = Rumble()
        repeat(80) { vad.observe(room.levelDb(hum(it * frame))) }
        var started = false
        repeat(20) { i ->
            val at = (80 + i) * frame
            if (vad.process(mix(hum(at), voice(400, at))) == EnergyVad.Change.Started) started = true
        }
        assertTrue(started)
    }

    @Test fun vadIgnoresShortClicks() {
        val vad = EnergyVad()
        repeat(20) { assertNull(vad.process(noise(100))) }
        assertNull(vad.process(voice(8000, 0)))
        repeat(20) { assertNull(vad.process(noise(100))) }
    }

    @Test fun ringKeepsTheTailAddressedByAbsolutePosition() {
        val ring = AudioRing(capacity = 10)
        ring.write(ShortArray(7) { it.toShort() })
        ring.write(ShortArray(7) { (7 + it).toShort() })
        assertEquals(14, ring.total)
        assertArrayEquals(ShortArray(4) { (10 + it).toShort() }, ring.since(10))
        assertArrayEquals(ShortArray(10) { (4 + it).toShort() }, ring.since(0))  // clamped to what is kept
        assertArrayEquals(ShortArray(0), ring.since(20))
        assertArrayEquals(shortArrayOf(12, 13), ring.last(2))
    }

    @Test fun pcmRoundTrip() {
        val s = shortArrayOf(0, 1, -1, 32767, -32768, 12345)
        assertArrayEquals(s, s.toLittleEndian().toShorts())
        assertArrayEquals(byteArrayOf(0x39, 0x30), shortArrayOf(12345).toLittleEndian())
    }

    @Test fun theWakeWordEndIsFoundWhicheverWayVoskCounts() {
        val r = SAMPLE_RATE.toLong()
        // counted from the recognizer's start: 60 s fed in all, 3 s since the reset, the word ended 1 s ago
        assertEquals(1_000 * r - r, wakeEndInRing(59 * r, 60 * r, 3 * r, 1_000 * r, RING_SAMPLES))
        // counted from the reset: the word ended at 2 s of the 3 s since it
        assertEquals(1_000 * r - r, wakeEndInRing(2 * r, 60 * r, 3 * r, 1_000 * r, RING_SAMPLES))
        // neither lies in what is kept
        assertEquals(null, wakeEndInRing(500 * r, 60 * r, 3 * r, 1_000 * r, RING_SAMPLES))
    }
}
