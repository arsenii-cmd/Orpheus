package dev.arco.orpheus

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.vosk.LibVosk
import org.vosk.LogLevel
import org.vosk.Model
import org.vosk.Recognizer
import java.io.File
import kotlin.concurrent.thread

/**
 * The assistant itself: a foreground service with the microphone, so it keeps listening for
 * "Орфей" with the app closed and the screen off. All state changes go through [AssistantMachine]
 * on the main thread; the audio thread only reads the microphone, spots the wake word and streams.
 */
class OrpheusService : Service() {

    companion object {
        private const val ACTION_START = "start"
        private const val ACTION_STOP = "stop"
        private const val ACTION_TALK = "talk"
        private const val ACTION_PERSONAL = "personal"
        private const val ACTION_RESUME = "resume"
        private const val ACTION_ENROLL = "enroll"
        private const val ACTION_ENROLL_RESET = "enroll_reset"
        private const val EXTRA_ON = "on"
        private const val CHANNEL = "orpheus"
        private const val NOTIFICATION_ID = 1
        private const val FRAME = SAMPLE_RATE / 20  // 50 ms
        /** [streamFrom] / [pendingFrom] value meaning "from the frame being read now". */
        private const val NOW = -2L
        /** No conversation going on: by touch, the microphone is closed in these. */
        private val IDLE = setOf(Phase.Off, Phase.Wake, Phase.Paused)

        /** Not started without the microphone: a foreground service that never calls startForeground crashes the app. */
        fun start(context: Context) {
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                Bus.problem.value = "Нет доступа к микрофону"
                return
            }
            ContextCompat.startForegroundService(context, intent(context, ACTION_START))
        }
        fun stop(context: Context) = context.startService(intent(context, ACTION_STOP))
        fun talk(context: Context) = context.startService(intent(context, ACTION_TALK))
        /** The digital assistant was called (the buds' touch and hold): a conversation, Orpheus started if it was off. */
        fun summon(context: Context) {
            try {
                if (Bus.phase.value == Phase.Off) start(context)
                talk(context)  // queued after the start
            } catch (e: Exception) {  // started from the background where Android does not allow it
                Bus.problem.value = "Включи Орфея в приложении: ${e.javaClass.simpleName}"
            }
        }
        fun resume(context: Context) = context.startService(intent(context, ACTION_RESUME))
        /** Record one phrase of the owner's voice: like the talk button, but the server keeps the voice. */
        fun enrollPhrase(context: Context) = context.startService(intent(context, ACTION_ENROLL))
        fun enrollReset(context: Context) = context.startService(intent(context, ACTION_ENROLL_RESET))
        fun personal(context: Context, on: Boolean) =
            context.startService(intent(context, ACTION_PERSONAL).putExtra(EXTRA_ON, on))

        private fun intent(context: Context, action: String) =
            Intent(context, OrpheusService::class.java).setAction(action)
    }

    private val main = Handler(Looper.getMainLooper())
    /** Made anew by every begin(): a cancelled scope launches nothing (a quick off and on reuses the instance). */
    private var scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val machine = AssistantMachine()
    private lateinit var link: ServerLink
    private lateinit var musicPause: MusicPause
    private lateinit var player: Player
    private var settings = Settings()
    private var running = false
    private var wakeLock: PowerManager.WakeLock? = null

    // shared with the audio thread
    @Volatile private var phase = Phase.Off
    @Volatile private var recording = false
    /** Absolute sample to stream from, or -1 when not streaming. Set on main, read by audio. */
    @Volatile private var streamFrom = -1L
    /** Where the next utterance should start; the audio thread sets it right before an event. */
    @Volatile private var pendingFrom = NOW
    @Volatile private var wakeConfidence = Settings().wakeConfidence.toDouble()
    @Volatile private var model: Model? = null
    @Volatile private var record: AudioRecord? = null
    /** Called by the buds' touch (settings.trigger "touch"): the microphone only while a conversation goes on. */
    @Volatile private var touchMode = false
    /** Whether the microphone thread may read now: always with the wake word, per conversation by touch. */
    @Volatile private var micOpen = true
    private val micGate = Object()
    /** Changes with every open and close: a recording of an older one ends (a touch within its last read). */
    @Volatile private var micGen = 0
    @Volatile private var modelLoading = false
    /** A call is waiting for the buds' microphone (main thread). */
    private var summoning = false
    /** The number of the phrase sent last ("id" in start); the server marks its answer with it. */
    private var phraseId = 0
    /** The earbuds' microphone while they are connected and the setting allows it, else null (the phone's). */
    @Volatile private var headsetInput: android.media.AudioDeviceInfo? = null
    private val headset by lazy {
        HeadsetMic(this) { input ->
            headsetInput = input
            record?.preferredDevice = input
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        link = ServerLink(::onServer, ::onServerAudio) { dispatch(Event.Failure("связь с сервером потеряна")) }
        player = Player { done -> dispatch(done) }
        musicPause = MusicPause(this)
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Орфей", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Орфей слушает слово активации"
                setShowBadge(false)
            }
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                shutdown()
                return START_NOT_STICKY
            }
            ACTION_PERSONAL -> if (running) {
                val on = intent.getBooleanExtra(EXTRA_ON, false)
                if (!link.send(Protocol.mode(on))) Bus.problem.value = "Нет связи с сервером"
            }
            ACTION_RESUME -> if (running) dispatch(Event.Resume)
            ACTION_ENROLL -> if (running) {
                // Talk starts a phrase only from these; elsewhere the flag would stay and mark the next question
                if (machine.phase in setOf(Phase.Wake, Phase.FollowUp, Phase.Speaking)) {
                    whenMicReady { talk(enroll = true) }
                } else {
                    Bus.enroll.update { it.copy(error = "Подожди, пока Орфей договорит") }
                }
            }
            ACTION_ENROLL_RESET -> if (running) {
                if (!link.send(Protocol.enrollReset())) Bus.problem.value = "Нет связи с сервером"
            }
            // not running: a touch whose start was refused (no microphone) - nothing to keep this service for
            ACTION_TALK -> if (running) whenMicReady { talk() } else stopSelf()
            else -> if (!running) begin()
        }
        return START_NOT_STICKY
    }

    private fun begin() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            Bus.problem.value = "Нет доступа к микрофону"
            stopSelf()
            return
        }
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, notification(Phase.Wake),
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
        )
        running = true
        scope.cancel()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
        // held only while the microphone is read (always with the wake word, a conversation by touch)
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "orpheus:listen").apply { setReferenceCounted(false) }
        scope.launch {
            settingsFlow().collectLatest { s ->
                settings = s
                wakeConfidence = s.wakeConfidence.toDouble()
                touchMode = s.trigger == "touch"
                setMic(!touchMode || machine.phase !in IDLE || summoning)
                if (running) {  // the notification's text follows the setting at once
                    getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(machine.phase))
                }
                // the wake word's model only when it is listened for: by touch it is never fed
                if (!touchMode && model == null && !modelLoading) {
                    modelLoading = true
                    thread(name = "orpheus-model") { loadModel(); modelLoading = false }
                }
                machine.config = machine.config.copy(followUp = s.followUp, followUpMs = s.followUpSeconds * 1000L)
                link.connect(s.effectiveUrl, s.effectiveToken, s.personalKey)
            }
        }
        headset.start()
        recording = true
        thread(name = "orpheus-mic", priority = Thread.MAX_PRIORITY) { audioLoop() }
        dispatch(Event.Enable)
        warnIfSilent()
    }

    /**
     * Open or close the microphone (main thread). Closed: the microphone thread waits, the buds are let go
     * (music leaves the headset profile of a call) and the CPU may sleep.
     */
    private fun setMic(open: Boolean) {
        if (open != micOpen) micGen++
        micOpen = open
        // closed: the old recording is not "ready" for the next touch while its thread is still letting it go
        if (!open) record = null
        if (open && running) {
            main.removeCallbacks(ticker)
            main.post(ticker)
        }
        headset.setWanted(settings.headsetMic && open)
        wakeLock?.let { if (open && running) it.acquire() else if (it.isHeld) it.release() }
        synchronized(micGate) { micGate.notifyAll() }
    }

    /** A conversation by the talk button, the buds' touch or «Мой голос»: the microphone opened first (by touch
     *  it was closed), and the beep only once it hears through the buds (their link takes ~0.5 s). */
    private fun whenMicReady(then: () -> Unit) {
        if (micOpen && record != null) {
            then()
            return
        }
        setMic(true)
        summoning = true  // the settings arriving now (a cold start by touch) must not close it again
        val since = SystemClock.elapsedRealtime()
        main.post(object : Runnable {
            override fun run() {
                if (!running) return
                val waited = SystemClock.elapsedRealtime() - since
                if (!micOpen) {  // closed again meanwhile («Слушать снова» from a pause): no phrase into a closed mic
                    summoning = false
                    return
                }
                // the recording through the buds, and the server: a touch that started Orpheus from off waits
                // for its connection too (up to 5 s), or the first phrase would be "Нет связи с сервером"
                val heard = record != null && (hearingThroughBuds() || headsetInput == null && waited > 300)
                if ((heard || waited > 1_500) && (link.online || waited > 5_000)) {
                    summoning = false
                    then()
                } else {
                    main.postDelayed(this, 50)
                }
            }
        })
    }

    private fun talk(enroll: Boolean = false) {
        if (enroll) {
            if (machine.phase !in setOf(Phase.Wake, Phase.FollowUp, Phase.Speaking)) return
            Bus.enroll.update { it.copy(recording = true, error = null) }
        }
        pendingFrom = NOW
        dispatch(Event.Talk)
        if (touchMode && machine.phase in IDLE) setMic(false)  // the Talk did not start a conversation
    }

    /** The reply and the beeps go to the media volume; at zero Orpheus is silent and it looks broken. */
    private fun warnIfSilent() {
        val audio = getSystemService(android.media.AudioManager::class.java)
        if (audio.getStreamVolume(android.media.AudioManager.STREAM_MUSIC) == 0) {
            Bus.problem.value = "Громкость медиа на нуле — Орфея не будет слышно"
        }
    }

    private fun shutdown() {
        if (running) dispatch(Event.Disable)
        release()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** All a run holds, let go: whichever way the service ends (the system may destroy it without ACTION_STOP). */
    private fun release() {
        running = false
        recording = false
        synchronized(micGate) { micGate.notifyAll() }  // a microphone thread waiting for a touch ends too
        main.removeCallbacksAndMessages(null)  // the ticker and a pending wait for the buds
        summoning = false
        headset.stop()
        musicPause.hold(false)
        player.stop()
        link.close()
        scope.cancel()
        wakeLock?.let { if (it.isHeld) it.release() }
        wakeLock = null
        Bus.level.value = 0f
        Bus.personal.value = false
        Bus.forgetPersonal()
        phase = Phase.Off
        Bus.phase.value = Phase.Off  // summon() starts it again, whatever way it ended
    }

    override fun onDestroy() {
        release()
        super.onDestroy()
    }

    private val ticker = object : Runnable {
        override fun run() {
            dispatch(Event.Tick)
            // by touch between conversations nothing times out: no ticks (the CPU may sleep); setMic starts them again
            if (running && (micOpen || !touchMode)) main.postDelayed(this, 200)
        }
    }

    // ---- state machine, main thread only

    private fun dispatch(event: Event) {
        if (Looper.myLooper() != Looper.getMainLooper()) {
            main.post { dispatch(event) }
            return
        }
        val before = machine.phase
        val effects = machine.handle(event, SystemClock.elapsedRealtime())
        if (event != Event.Tick || machine.phase != before) {
            android.util.Log.i("Orpheus", "$before + $event -> ${machine.phase} $effects")
        }
        for (effect in effects) apply(effect)
        if (machine.phase != before) {
            phase = machine.phase
            // the owner's music waits while the conversation goes on (from «Орфей» to the end of the follow-up)
            musicPause.hold(phase in setOf(Phase.Listening, Phase.Thinking, Phase.Speaking, Phase.FollowUp))
            Bus.phase.value = machine.phase
            // by touch, the conversation is over: the microphone and the buds are let go until the next touch
            if (touchMode && machine.phase in IDLE && micOpen) setMic(false)
            // an enrollment phrase that never reached the server (cancelled, offline) ends here too
            if (machine.phase in IDLE) {
                if (Bus.enroll.value.recording) Bus.enroll.update { it.copy(recording = false) }
            }
            if (machine.phase != Phase.Off) {
                getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification(machine.phase))
            }
        }
    }

    private fun apply(effect: Effect) {
        when (effect) {
            is Effect.Play -> if (settings.earcons) Earcons.play(effect.earcon)
            is Effect.StartUtterance -> {
                if (!link.online) {
                    Bus.problem.value = if (Bus.link.value == Link.Denied) "Сервер не пустил: неверный токен" else "Нет связи с сервером"
                    main.post { dispatch(Event.Failure("offline")) }
                    return
                }
                val buds = hearingThroughBuds()
                phraseId++
                link.send(Protocol.start(
                    effect.followUp, settings.voice, enroll = Bus.enroll.value.recording,
                    // a conversation opened by the owner's hand (a touch on their buds, the button) is theirs: only a voice's is checked
                    headset = buds, strict = buds && settings.headsetStrict && !effect.byHand, id = phraseId,
                ))
                streamFrom = pendingFrom
                pendingFrom = NOW
            }
            Effect.FinishUtterance -> {
                streamFrom = -1
                link.send(Protocol.stop())
            }
            Effect.CancelUtterance -> {
                streamFrom = -1
                link.send(Protocol.cancel())
            }
            Effect.StopPlayback -> player.stop()
        }
    }

    /** Is the recording really coming from the earbuds? What was asked for (headsetInput) may not be what the
     *  system gave (the link not up yet, a call holding it): strict mode must follow the truth. */
    private fun hearingThroughBuds(): Boolean {
        val routed = record?.routedDevice ?: return false
        return routed.type in HeadsetMic.BUDS
    }

    // ---- server, main thread

    private fun onServer(message: ServerMessage) {
        // a reply cut short by the talk button may still be coming in after the next phrase went out: not its answer
        val about = when (message) {
            is ServerMessage.Transcript -> message.id
            is ServerMessage.Reply -> message.id
            is ServerMessage.AudioStart -> message.id
            is ServerMessage.AudioEnd -> message.id
            is ServerMessage.Error -> message.id
            else -> null
        }
        if (about != null && about != phraseId) return
        when (message) {
            ServerMessage.EndOfSpeech -> dispatch(Event.SpeechEnded)
            is ServerMessage.Transcript -> Bus.say(true, message.text)
            is ServerMessage.Reply -> Bus.append(message.text)
            is ServerMessage.AudioStart -> if (phase == Phase.Thinking || phase == Phase.Listening) {
                if (phase == Phase.Listening) dispatch(Event.SpeechEnded)
                player.begin(message.sampleRate)
                dispatch(Event.ReplyAudio)
            }
            is ServerMessage.AudioEnd -> {
                if (message.notOwner) {
                    // said, heard and not answered: without a sound it looked like Orpheus had hung
                    if (settings.earcons) Earcons.play(Earcon.NotOwner)
                    Bus.problem.value = "Не узнал голос — повтори"
                }
                val done = Event.ReplyDone(message.expectReply, message.listen, message.pause)
                when (phase) {
                    Phase.Speaking -> player.end(done)
                    Phase.Thinking -> dispatch(done)
                    else -> {}
                }
            }
            is ServerMessage.Error -> {
                Bus.problem.value = message.message
                dispatch(Event.Failure(message.message))
            }
            is ServerMessage.Mode -> {
                Bus.personal.value = message.personal
                if (!message.personal) Bus.forgetPersonal()
            }
            is ServerMessage.Enroll -> Bus.enroll.update {
                it.copy(count = message.count, needed = message.needed, mode = message.mode, error = message.error, recording = false)
            }
            is ServerMessage.Unknown -> {}
        }
        // what was said never goes to the system log (anyone with USB could read it, "Личное" too): its length only
        val logged = if (message is ServerMessage.Transcript) "Transcript(${message.text.length} символов)" else "$message"
        if (message !is ServerMessage.Reply) android.util.Log.i("Orpheus", "server: $logged (phase $phase)")
    }

    private fun onServerAudio(pcm: ByteArray) {
        if (phase == Phase.Speaking) player.chunk(pcm)
    }

    // ---- microphone, its own thread

    private fun loadModel() {
        try {
            Bus.problem.value = "Готовлю распознавание слова «Орфей»…"
            LibVosk.setLogLevel(LogLevel.WARNINGS)
            model = Model(ModelFiles.ensure(this).absolutePath)
            if (Bus.problem.value?.startsWith("Готовлю") == true) Bus.problem.value = null
        } catch (e: Exception) {
            Bus.problem.value = "Модель слова активации не загрузилась: ${e.message}"
        }
    }

    /** The microphone for as long as Orpheus is on: reopened when it fails (the buds gone, the audio server restarted). */
    private fun audioLoop() {
        while (recording) {
            // by touch, between conversations: no microphone at all, the thread sleeps until a touch
            synchronized(micGate) {
                while (recording && !micOpen) micGate.wait()
            }
            if (!recording) return
            when (listen()) {
                MicEnd.Off -> return
                MicEnd.Broken -> if (recording) Thread.sleep(500)  // a pause before opening it again
                MicEnd.Closed -> {}  // between conversations: opened again at once on the next touch
            }
        }
    }

    /** Reads the microphone until Orpheus is off (false), the recording breaks or the microphone is closed
     *  between conversations (true: the loop above opens it again when it may). */
    private enum class MicEnd { Off, Broken, Closed }

    @SuppressLint("MissingPermission")  // checked in begin()
    private fun listen(): MicEnd {
        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val record = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION, SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuffer, FRAME * 2 * 8),
        )
        if (record.state != AudioRecord.STATE_INITIALIZED) {
            Bus.problem.value = "Микрофон занят другим приложением"
            record.release()
            return MicEnd.Broken  // a call or another app's recording: tried again, not given up for good
        }
        this.record = record  // first: a route change from now on reaches it (HeadsetMic's callback)
        record.preferredDevice = headsetInput
        var broken = false
        val ring = AudioRing()
        val vad = EnergyVad()
        var listenMaxDb = -120.0
        val frame = ShortArray(FRAME)
        var recognizer: Recognizer? = null
        var fedAll = 0L          // samples fed to the recognizer since it was made
        var fedSinceReset = 0L   // ... and since its last reset
        var lastPhase = Phase.Off
        var phaseSince = 0L
        var sentUpTo = -1L
        var levelAt = 0L
        val room = Rumble()
        record.startRecording()
        val gen = micGen
        try {
            while (recording && micOpen && gen == micGen) {
                val n = record.read(frame, 0, FRAME)
                if (n < 0) {  // ERROR_DEAD_OBJECT and the like: every next read fails the same, at full speed
                    android.util.Log.w("Orpheus", "mic: read error $n, reopening")
                    broken = true
                    break
                }
                if (n == 0) continue
                ring.write(frame, n)
                val now = SystemClock.elapsedRealtime()
                val frameDb = room.levelDb(frame, n)  // without the rumble, as the VAD measures it
                if (now - levelAt > 60) {
                    levelAt = now
                    Bus.level.value = ((frameDb + 60) / 50).coerceIn(0.0, 1.0).toFloat()
                }

                val p = phase
                if (p == Phase.Listening) listenMaxDb = maxOf(listenMaxDb, frameDb)
                if (p != lastPhase) {
                    if (lastPhase == Phase.Listening) {
                        // what the phone heard: a phrase not sent is explained by these two numbers
                        android.util.Log.i("Orpheus", "listen: loudest %.0f dB, room %.0f dB".format(listenMaxDb, vad.floorDb))
                        listenMaxDb = -120.0
                    }
                    if (p == Phase.Wake) {
                        recognizer?.reset()
                        fedSinceReset = 0
                    }
                    if (p == Phase.Listening || p == Phase.FollowUp) vad.reset()
                    lastPhase = p
                    phaseSince = now
                }
                if (streamFrom == NOW) streamFrom = ring.total - n

                // Listening and FollowUp feed the VAD through process(); elsewhere it only learns the room
                // (not while Orpheus is talking: that is its own voice, not the background)
                if (p == Phase.Wake || p == Phase.Thinking) vad.observe(frameDb)

                when (p) {
                    Phase.Wake -> {
                        if (touchMode) continue  // by touch there is no wake word (the moment between the touch and the beep)
                        val m = model ?: continue
                        val r = recognizer ?: Recognizer(m, SAMPLE_RATE.toFloat(), WAKE_GRAMMAR).also {
                            it.setWords(true)
                            recognizer = it
                            fedAll = 0
                            fedSinceReset = 0
                        }
                        fedAll += n
                        fedSinceReset += n
                        if (r.acceptWaveForm(frame, n)) {
                            val end = findWakeWord(r.result, wakeConfidence)
                            if (end != null) {
                                pendingFrom = wakeEndInRing(end, fedAll, fedSinceReset, ring.total, RING_SAMPLES) ?: ring.total
                                r.reset()
                                fedSinceReset = 0
                                dispatch(Event.WakeWord)
                            }
                        }
                    }
                    // The first moments of the follow-up window still carry the tail of the reply:
                    // those frames are not shown to the VAD at all. (Showing them and dropping the
                    // "started" it reports would lose the whole phrase if the owner answers at once:
                    // the VAD says "started" only once per stretch of speech.)
                    Phase.FollowUp -> if (now - phaseSince >= machine.config.followUpGraceMs &&
                        vad.process(frame, n) == EnergyVad.Change.Started) {
                        pendingFrom = ring.total - SAMPLE_RATE / 2  // the start of the word that woke the VAD
                        dispatch(Event.SpeechStarted)
                    }
                    Phase.Listening -> {
                        val from = streamFrom
                        if (from >= 0) {
                            // everything since the utterance start that has not gone out yet, in VAD-sized pieces
                            val start = if (sentUpTo >= from) sentUpTo else from
                            val chunk = ring.since(start)
                            sentUpTo = ring.total
                            link.send(chunk.toLittleEndian())
                            var i = 0
                            while (i < chunk.size) {
                                val len = minOf(FRAME, chunk.size - i)
                                when (vad.process(chunk.copyOfRange(i, i + len), len)) {
                                    EnergyVad.Change.Started -> dispatch(Event.SpeechStarted)
                                    EnergyVad.Change.Ended -> dispatch(Event.SpeechEnded)
                                    null -> {}
                                }
                                i += len
                            }
                        }
                    }
                    else -> {}
                }
                if (streamFrom < 0) sentUpTo = -1
            }
        } finally {
            this.record = null
            record.stop()
            record.release()
            recognizer?.close()
        }
        return when {
            broken -> MicEnd.Broken
            recording -> MicEnd.Closed  // closed between conversations: opened again on the next touch
            else -> MicEnd.Off
        }
    }

    // ---- notification

    private fun notification(phase: Phase): Notification {
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        fun action(action: String, code: Int) = PendingIntent.getService(
            this, code, intent(this, action), PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Орфей")
            .setContentText(if (phase == Phase.Wake && touchMode) "Зажми наушник, чтобы спросить" else phase.label())
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
        if (phase == Phase.Paused) builder.addAction(0, "Слушать снова", action(ACTION_RESUME, 3))
        return builder
            .addAction(0, "Говорить", action(ACTION_TALK, 1))
            .addAction(0, "Выключить", action(ACTION_STOP, 2))
            .build()
    }
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

/** The Vosk model ships in the APK's assets; Vosk needs it as plain files, so it is copied once. */
object ModelFiles {
    private const val ASSET_DIR = "vosk-model"

    fun ensure(context: Context): File {
        val version = context.packageManager.getPackageInfo(context.packageName, 0).longVersionCode
        val dir = File(context.filesDir, "$ASSET_DIR-$version")
        val done = File(dir, ".done")
        if (done.exists()) return dir
        context.filesDir.listFiles()?.filter { it.name.startsWith(ASSET_DIR) }?.forEach { it.deleteRecursively() }
        copy(context, ASSET_DIR, dir)
        if (!File(dir, "am/final.mdl").exists()) error("в APK нет модели (android/scripts/fetch_wake_model.sh)")
        done.writeText("ok")
        return dir
    }

    private fun copy(context: Context, asset: String, target: File) {
        val children = context.assets.list(asset).orEmpty()
        if (children.isEmpty()) {
            target.parentFile?.mkdirs()
            context.assets.open(asset).use { input -> target.outputStream().use { input.copyTo(it) } }
        } else {
            target.mkdirs()
            for (child in children) copy(context, "$asset/$child", File(target, child))
        }
    }
}
