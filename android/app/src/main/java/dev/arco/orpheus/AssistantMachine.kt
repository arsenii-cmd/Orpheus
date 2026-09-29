package dev.arco.orpheus

/**
 * What Orpheus is doing, as a smart speaker would show it with its light ring.
 * The machine is pure: no Android, no threads. The service feeds it events and carries out
 * the effects it returns; time comes in with every event, so tests control the clock.
 */
enum class Phase { Off, Wake, Listening, Thinking, Speaking, FollowUp, Paused }

enum class Earcon { Start, Done, Cancel, Error, Thinking }

sealed interface Event {
    data object Enable : Event
    data object Disable : Event
    /** The wake word was heard. */
    data object WakeWord : Event
    /** The talk button (app or notification): the same as the wake word, from any phase but Off. */
    data object Talk : Event
    /** The local VAD heard speech start (in Listening and FollowUp). */
    data object SpeechStarted : Event
    /** The phrase is over: local silence or the server's end_of_speech. */
    data object SpeechEnded : Event
    /** The first chunk of the spoken reply arrived. */
    data object ReplyAudio : Event
    /** The reply finished playing (or came without audio). */
    data class ReplyDone(val expectReply: Boolean, val listen: Boolean = true, val pause: Boolean = false) : Event
    /** «Слушать снова» (the notification or the app): out of the pause, waiting for «Орфей» again. */
    data object Resume : Event
    data class Failure(val message: String) : Event
    data object Tick : Event
}

sealed interface Effect {
    /** Send "start" and begin streaming the microphone (with the audio already buffered). */
    data class StartUtterance(val followUp: Boolean) : Effect
    data object FinishUtterance : Effect
    data object CancelUtterance : Effect
    data object StopPlayback : Effect
    data class Play(val earcon: Earcon) : Effect
}

data class MachineConfig(
    val followUp: Boolean = true,
    val followUpMs: Long = 6_000,
    /** After the wake word, how long to wait for any speech before giving up. */
    val noSpeechMs: Long = 6_000,
    /** Only a guard against a microphone stuck "open": a phrase ends at a pause (15 s cut a long one off). */
    val maxUtteranceMs: Long = 120_000,
    val replyTimeoutMs: Long = 30_000,
    /** A reply that "plays" longer than this is stuck (the player never reported the end). */
    val maxSpeakingMs: Long = 90_000,
    /** At the start of FollowUp the microphone may still hear the end of the reply. */
    val followUpGraceMs: Long = 400,
    /** While the reply is being thought of, a soft tick this often: "still working on it". */
    val thinkingTickMs: Long = 1_500,
)

class AssistantMachine(var config: MachineConfig = MachineConfig()) {
    var phase: Phase = Phase.Off
        private set

    private var since = 0L        // when the current phase began
    private var heardSpeech = false
    private var tickedAt = 0L     // the last "thinking" tick

    /** Whether a SpeechStarted in FollowUp should count yet (see [MachineConfig.followUpGraceMs]).
     *  The service also keeps those first frames away from the VAD, so this is only a backstop. */
    fun followUpArmed(now: Long) = phase == Phase.FollowUp && now - since >= config.followUpGraceMs

    fun handle(event: Event, now: Long): List<Effect> {
        val effects = mutableListOf<Effect>()
        when (event) {
            Event.Enable -> if (phase == Phase.Off) go(Phase.Wake, now)
            Event.Disable -> {
                when (phase) {
                    Phase.Listening, Phase.Thinking -> effects += Effect.CancelUtterance
                    Phase.Speaking -> effects += Effect.StopPlayback
                    else -> {}
                }
                go(Phase.Off, now)
            }
            Event.WakeWord -> if (phase == Phase.Wake) startListening(now, followUp = false, effects)
            Event.Resume -> if (phase == Phase.Paused) go(Phase.Wake, now)
            Event.Talk -> when (phase) {
                Phase.Wake, Phase.FollowUp, Phase.Paused -> startListening(now, followUp = false, effects)
                Phase.Speaking -> {
                    effects += Effect.StopPlayback
                    startListening(now, followUp = false, effects)
                }
                else -> {}
            }
            Event.SpeechStarted -> when (phase) {
                Phase.Listening -> heardSpeech = true
                Phase.FollowUp -> if (followUpArmed(now)) {
                    go(Phase.Listening, now)
                    heardSpeech = true
                    effects += Effect.StartUtterance(followUp = true)
                }
                else -> {}
            }
            Event.SpeechEnded -> if (phase == Phase.Listening) finishListening(now, effects)
            Event.ReplyAudio -> if (phase == Phase.Thinking) go(Phase.Speaking, now)
            is Event.ReplyDone -> if (phase == Phase.Thinking || phase == Phase.Speaking) {
                when {
                    event.pause -> go(Phase.Paused, now)
                    event.listen && (config.followUp || event.expectReply) -> go(Phase.FollowUp, now)
                    else -> go(Phase.Wake, now)
                }
            }
            is Event.Failure -> when (phase) {
                Phase.Listening, Phase.Thinking -> {
                    effects += Effect.CancelUtterance
                    effects += Effect.Play(Earcon.Error)
                    go(Phase.Wake, now)
                }
                Phase.Speaking -> {
                    effects += Effect.StopPlayback
                    effects += Effect.Play(Earcon.Error)
                    go(Phase.Wake, now)
                }
                else -> {}
            }
            Event.Tick -> tick(now, effects)
        }
        return effects
    }

    private fun tick(now: Long, effects: MutableList<Effect>) {
        val elapsed = now - since
        when (phase) {
            Phase.Listening -> when {
                // the phone's VAD heard nothing, yet the owner may have spoken quietly ("Орфей" was
                // caught, the phrase after it was not, and it was thrown away): the server's recogniser decides
                !heardSpeech && elapsed >= config.noSpeechMs -> finishListening(now, effects)
                elapsed >= config.maxUtteranceMs -> finishListening(now, effects)
            }
            Phase.Thinking -> if (elapsed >= config.replyTimeoutMs) {
                effects += Effect.CancelUtterance
                effects += Effect.Play(Earcon.Error)
                go(Phase.Wake, now)
            } else if (elapsed >= config.thinkingTickMs && now - tickedAt >= config.thinkingTickMs) {
                tickedAt = now
                effects += Effect.Play(Earcon.Thinking)
            }
            Phase.Speaking -> if (elapsed >= config.maxSpeakingMs) {
                effects += Effect.StopPlayback
                go(Phase.Wake, now)
            }
            Phase.FollowUp -> if (elapsed >= config.followUpMs) go(Phase.Wake, now)
            else -> {}
        }
    }

    private fun startListening(now: Long, followUp: Boolean, effects: MutableList<Effect>) {
        go(Phase.Listening, now)
        effects += Effect.Play(Earcon.Start)
        effects += Effect.StartUtterance(followUp)
    }

    private fun finishListening(now: Long, effects: MutableList<Effect>) {
        effects += Effect.FinishUtterance
        effects += Effect.Play(Earcon.Done)
        go(Phase.Thinking, now)
    }

    private fun go(next: Phase, now: Long) {
        phase = next
        since = now
        tickedAt = now
        heardSpeech = false
    }
}
