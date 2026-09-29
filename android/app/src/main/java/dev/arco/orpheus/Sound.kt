package dev.arco.orpheus

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import java.util.concurrent.LinkedBlockingQueue
import kotlin.concurrent.thread
import kotlin.math.PI
import kotlin.math.sin

// USAGE_MEDIA, not USAGE_ASSISTANT: on Samsung the assistant usage has a volume of its own that
// the volume keys do not reach, and it sat at zero — every reply "played" in silence.
private val ASSISTANT_AUDIO: AudioAttributes = AudioAttributes.Builder()
    .setUsage(AudioAttributes.USAGE_MEDIA)
    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
    .build()

/**
 * Music or a video playing on the phone is paused while Orpheus listens, thinks and talks, and goes on
 * afterwards: the transient audio focus voice assistants take (the players pause on losing it).
 */
class MusicPause(context: Context, private val ourSound: () -> Boolean) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE)
        .setAudioAttributes(ASSISTANT_AUDIO)
        .setAcceptsDelayedFocusGain(true)
        .setOnAudioFocusChangeListener(::onFocusChange, main)
        .build()
    /** Wanted: a conversation is going on. Granted: the system gave the focus (it may refuse, or take it back). */
    private var held = false
    private var granted = false
    /** Music was playing when the conversation began: only then may it be paused by a key, and played again. */
    private var musicWas = false
    private var pausedByKey = false

    fun hold(on: Boolean) {
        if (on == held) return
        held = on
        main.removeCallbacks(check)
        if (on) {
            musicWas = audio.isMusicActive  // before our beep: the owner's music, not us
            take()
            // a player that keeps on after losing the focus (it happened: the music played while Orpheus listened)
            main.postDelayed(check, 500)
            main.postDelayed(check, 1_500)
        } else {
            granted = false
            audio.abandonAudioFocusRequest(request)
            if (pausedByKey) key(android.view.KeyEvent.KEYCODE_MEDIA_PLAY)
            pausedByKey = false
            musicWas = false
        }
    }

    /** Called while the conversation goes on (the ticks): the focus asked for again if it was refused or taken,
     *  and music still heard (only while Orpheus itself is silent: its own beeps and replies are "music" too)
     *  paused by the media key - and played again afterwards, as it was playing before. */
    fun keep() {
        if (!held) return
        if (!granted && !inCall()) take()
        if (musicWas && !pausedByKey && !ourSound() && audio.isMusicActive) pauseByKey()
    }

    private val check = Runnable { keep() }

    private fun take() {
        granted = audio.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    private fun onFocusChange(change: Int) {
        when (change) {
            AudioManager.AUDIOFOCUS_GAIN -> granted = true
            // another app took it (a player resuming by itself): asked for again on the next tick, unless a call
            AudioManager.AUDIOFOCUS_LOSS, AudioManager.AUDIOFOCUS_LOSS_TRANSIENT,
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> granted = false
        }
    }

    private fun pauseByKey() {
        pausedByKey = true
        key(android.view.KeyEvent.KEYCODE_MEDIA_PAUSE)
    }

    private fun key(code: Int) {
        audio.dispatchMediaKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN, code))
        audio.dispatchMediaKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_UP, code))
    }

    private fun inCall() = audio.mode == AudioManager.MODE_IN_CALL || audio.mode == AudioManager.MODE_RINGTONE
}

/**
 * The spoken reply: PCM chunks are queued as they arrive and played on their own thread.
 * [onFinished] fires on the main thread once the last queued sample has actually been heard.
 */
class Player(private val onFinished: (Event.ReplyDone) -> Unit) {
    private sealed interface Item {
        class Begin(val rate: Int) : Item
        class Chunk(val pcm: ByteArray) : Item
        class End(val done: Event.ReplyDone) : Item
        data object Stop : Item
    }

    private val queue = LinkedBlockingQueue<Item>()
    private val main = Handler(Looper.getMainLooper())
    @Volatile private var generation = 0

    init {
        thread(name = "orpheus-player", isDaemon = true) { loop() }
    }

    fun begin(sampleRate: Int) = queue.put(Item.Begin(sampleRate))
    fun chunk(pcm: ByteArray) = queue.put(Item.Chunk(pcm))
    fun end(done: Event.ReplyDone) = queue.put(Item.End(done))

    /** Drop everything queued and cut the current reply short; [onFinished] is not called. */
    fun stop() {
        generation++
        queue.clear()
        queue.put(Item.Stop)
    }

    private fun loop() {
        var track: AudioTrack? = null
        var written = 0L
        var gen = generation
        while (true) {
            val item = queue.take()
            if (gen != generation) {  // a stop happened: whatever this reply had is void
                track?.release()
                track = null
                gen = generation
                if (item !is Item.Begin) continue
            }
            when (item) {
                is Item.Begin -> {
                    track?.release()
                    track = AudioTrack.Builder()
                        .setAudioAttributes(ASSISTANT_AUDIO)
                        .setAudioFormat(
                            AudioFormat.Builder()
                                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                                .setSampleRate(item.rate)
                                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                                .build()
                        )
                        .setTransferMode(AudioTrack.MODE_STREAM)
                        .setBufferSizeInBytes(
                            AudioTrack.getMinBufferSize(item.rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT) * 4
                        )
                        .build()
                    track.play()
                    written = 0
                }
                is Item.Chunk -> track?.let {
                    it.write(item.pcm, 0, item.pcm.size)
                    written += item.pcm.size / 2
                }
                is Item.End -> {
                    track?.let { t ->
                        // wait until the hardware has played what was written, unless stopped
                        while (gen == generation && t.playbackHeadPosition < written) Thread.sleep(20)
                        t.release()
                    }
                    track = null
                    if (gen == generation) main.post { onFinished(item.done) }
                }
                Item.Stop -> {
                    track?.pause()
                    track?.flush()
                    track?.release()
                    track = null
                }
            }
        }
    }
}

/** Until when Orpheus's own short sounds play (beeps, a reminder): to the system they are "music" too. */
object OwnSound {
    @Volatile var until = 0L

    fun playing(ms: Long) {
        until = maxOf(until, android.os.SystemClock.uptimeMillis() + ms + 100)
    }

    val busy get() = android.os.SystemClock.uptimeMillis() < until
}

/** A whole utterance played at once (a reminder the server said by itself), on the same stream as the replies. */
object Announcer {
    fun play(pcm: ByteArray, rate: Int, onDone: () -> Unit) {
        if (pcm.size < 2) return onDone()
        OwnSound.playing(pcm.size / 2 * 1000L / rate)
        val track = AudioTrack.Builder()
            .setAudioAttributes(ASSISTANT_AUDIO)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(rate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(pcm.size)
            .build()
        track.write(pcm, 0, pcm.size)
        track.play()
        Handler(Looper.getMainLooper()).postDelayed({ track.release(); onDone() }, pcm.size / 2 * 1000L / rate + 300)
    }
}

/** Short tones instead of sound files: rising means "I'm listening", falling means "got it". */
object Earcons {
    private const val RATE = 22_050

    private fun tone(vararg notes: Pair<Double, Int>, volume: Double = 0.25): ShortArray {
        val out = ArrayList<Short>()
        for ((freq, ms) in notes) {
            val n = RATE * ms / 1000
            val fade = RATE * 8 / 1000
            for (i in 0 until n) {
                val env = minOf(1.0, i / fade.toDouble(), (n - i) / fade.toDouble())
                val v = sin(2 * PI * freq * i / RATE) * env * volume
                out += (v * 32767).toInt().toShort()
            }
        }
        return out.toShortArray()
    }

    private val sounds = mapOf(
        Earcon.Start to tone(660.0 to 70, 990.0 to 110),
        Earcon.Done to tone(880.0 to 70, 587.0 to 100),
        Earcon.Cancel to tone(440.0 to 120, volume = 0.18),
        Earcon.Error to tone(330.0 to 110, 0.0 to 50, 330.0 to 160),
        Earcon.Thinking to tone(740.0 to 35, volume = 0.08),  // a soft tick while the reply is thought of
        Earcon.NotOwner to tone(520.0 to 90, 0.0 to 40, 390.0 to 90, 0.0 to 40, 290.0 to 160),  // "didn't know the voice"
    )

    fun play(earcon: Earcon) {
        val pcm = sounds.getValue(earcon)
        OwnSound.playing(pcm.size * 1000L / RATE)
        val track = AudioTrack.Builder()
            .setAudioAttributes(ASSISTANT_AUDIO)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(pcm.size * 2)
            .build()
        track.write(pcm, 0, pcm.size)
        track.play()
        Handler(Looper.getMainLooper()).postDelayed({ track.release() }, pcm.size * 1000L / RATE + 200)
    }
}
