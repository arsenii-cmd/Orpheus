package dev.arco.orpheus

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.SpeechRecognizer
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

/**
 * Orpheus as the phone's digital assistant (Settings → Apps → Default apps → Digital assistant app):
 * what the Galaxy Buds' "touch and hold → Digital assistant" and the side key's long press call.
 *
 * A call is only a "listen" for the running Orpheus (OrpheusService.summon): it opens the microphone
 * for one conversation, so between conversations there is no microphone at all — no battery spent on
 * it, and the buds play music in full quality instead of the headset profile of a call.
 *
 * The always-on "Орфей" of the system (AlwaysOnHotwordDetector, HotwordDetectionService) is not here:
 * those are @SystemApi, for preinstalled assistants with a keyword model on the DSP.
 */
class OrpheusAssistant : VoiceInteractionService()

/**
 * What the Galaxy Buds' "touch and hold → Digital assistant" really sends: android.intent.action.VOICE_COMMAND
 * (the system asked "Google or Perplexity?" — Orpheus was not among them). Shows nothing: summons
 * Orpheus and closes. An activity in front is also what Android wants for starting the microphone service.
 */
class VoiceCommandActivity : android.app.Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        OrpheusService.summon(this)
        finish()
    }
}

class OrpheusSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = OrpheusSession(this)
}

/** Shows nothing: the conversation happens by voice, the panel would only cover the screen. */
class OrpheusSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        OrpheusService.summon(context)
        hide()
    }
}

/**
 * The speech recognizer an assistant must bring along, and which becomes the phone's default with it.
 * Orpheus recognises on its server, not here: every request is handed on to the recognizer the phone
 * had before (Google's), so voice typing in other apps keeps working.
 */
class OrpheusRecognition : RecognitionService() {
    private var inner: SpeechRecognizer? = null

    override fun onStartListening(intent: Intent, callback: Callback) {
        inner?.destroy()
        inner = null
        val recognizer = recognizer(callback)
        if (recognizer == null) {
            callback.error(SpeechRecognizer.ERROR_RECOGNIZER_BUSY)
            return
        }
        inner = recognizer
        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = callback.readyForSpeech(params ?: Bundle())
            override fun onBeginningOfSpeech() = callback.beginningOfSpeech()
            override fun onRmsChanged(rmsdB: Float) = callback.rmsChanged(rmsdB)
            override fun onBufferReceived(buffer: ByteArray?) = callback.bufferReceived(buffer ?: ByteArray(0))
            override fun onEndOfSpeech() = callback.endOfSpeech()
            override fun onError(error: Int) = callback.error(error)
            override fun onResults(results: Bundle?) = callback.results(results ?: Bundle())
            override fun onPartialResults(partialResults: Bundle?) = callback.partialResults(partialResults ?: Bundle())
            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        recognizer.startListening(intent)
    }

    override fun onStopListening(callback: Callback) {
        inner?.stopListening()
    }

    override fun onCancel(callback: Callback) {
        inner?.cancel()
    }

    override fun onDestroy() {
        inner?.destroy()
        inner = null
        super.onDestroy()
    }

    /** Google's recognizer if it is installed, else any other one but this; on-device as the last resort. On
     *  Android 12+ in the name of the app that asked (its microphone permission, its being in front), not Orpheus'. */
    private fun recognizer(callback: Callback): SpeechRecognizer? {
        val context = if (android.os.Build.VERSION.SDK_INT >= 31) {
            createContext(android.content.ContextParams.Builder().setNextAttributionSource(callback.callingAttributionSource).build())
        } else this
        val others = packageManager.queryIntentServices(Intent(RecognitionService.SERVICE_INTERFACE), 0)
            .map { ComponentName(it.serviceInfo.packageName, it.serviceInfo.name) }
            .filter { it.packageName != packageName }
        val chosen = others.firstOrNull { it == GOOGLE } ?: others.firstOrNull { it.packageName.startsWith("com.google.") } ?: others.firstOrNull()
        if (chosen != null) return SpeechRecognizer.createSpeechRecognizer(context, chosen)
        if (android.os.Build.VERSION.SDK_INT >= 31 && SpeechRecognizer.isOnDeviceRecognitionAvailable(context)) {
            return SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        }
        return null
    }

    private companion object {
        // what the phone had as its default recognizer before Orpheus (a Samsung phone)
        val GOOGLE = ComponentName("com.google.android.tts", "com.google.android.apps.speech.tts.googletts.service.GoogleTTSRecognitionService")
    }
}
