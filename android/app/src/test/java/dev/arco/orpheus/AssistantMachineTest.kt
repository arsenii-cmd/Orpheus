package dev.arco.orpheus

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AssistantMachineTest {
    private val m = AssistantMachine(MachineConfig(followUp = true, followUpMs = 6_000, noSpeechMs = 5_000, maxUtteranceMs = 15_000, replyTimeoutMs = 30_000, followUpGraceMs = 400))

    private fun on(): AssistantMachine { m.handle(Event.Enable, 0); return m }

    @Test fun fullTurnWithFollowUp() {
        on()
        assertEquals(Phase.Wake, m.phase)
        assertEquals(listOf(Effect.Play(Earcon.Start), Effect.StartUtterance(false)), m.handle(Event.WakeWord, 1_000))
        assertEquals(Phase.Listening, m.phase)
        m.handle(Event.SpeechStarted, 1_200)
        assertEquals(listOf(Effect.FinishUtterance, Effect.Play(Earcon.Done)), m.handle(Event.SpeechEnded, 3_000))
        assertEquals(Phase.Thinking, m.phase)
        m.handle(Event.ReplyAudio, 4_000)
        assertEquals(Phase.Speaking, m.phase)
        m.handle(Event.ReplyDone(false), 6_000)
        assertEquals(Phase.FollowUp, m.phase)
        // the tail of the reply in the microphone does not count
        assertTrue(m.handle(Event.SpeechStarted, 6_100).isEmpty())
        assertEquals(Phase.FollowUp, m.phase)
        assertEquals(listOf(Effect.StartUtterance(true)), m.handle(Event.SpeechStarted, 7_000))
        assertEquals(Phase.Listening, m.phase)
    }

    @Test fun followUpTimesOutBackToWakeWord() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechStarted, 0); m.handle(Event.SpeechEnded, 1_000)
        m.handle(Event.ReplyDone(false), 2_000)
        m.handle(Event.Tick, 7_999)
        assertEquals(Phase.FollowUp, m.phase)
        m.handle(Event.Tick, 8_000)
        assertEquals(Phase.Wake, m.phase)
    }

    @Test fun withoutFollowUpGoesBackToWakeUnlessServerExpectsAnswer() {
        m.config = m.config.copy(followUp = false)
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000); m.handle(Event.ReplyDone(false), 2_000)
        assertEquals(Phase.Wake, m.phase)
        m.handle(Event.WakeWord, 3_000); m.handle(Event.SpeechEnded, 4_000); m.handle(Event.ReplyDone(true), 5_000)
        assertEquals(Phase.FollowUp, m.phase)
    }

    @Test fun nothingHeardAfterWakeWordStillGoesToTheServer() {
        on()
        m.handle(Event.WakeWord, 0)
        assertEquals(listOf(Effect.FinishUtterance, Effect.Play(Earcon.Done)), m.handle(Event.Tick, 5_000))
        assertEquals(Phase.Thinking, m.phase)
    }

    @Test fun longPhraseIsCutAtTheLimit() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechStarted, 100)
        assertTrue(m.handle(Event.Tick, 14_999).isEmpty())
        assertEquals(listOf(Effect.FinishUtterance, Effect.Play(Earcon.Done)), m.handle(Event.Tick, 15_000))
    }

    @Test fun noReplyIsAnError() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000)
        assertEquals(listOf(Effect.CancelUtterance, Effect.Play(Earcon.Error)), m.handle(Event.Tick, 31_000))
        assertEquals(Phase.Wake, m.phase)
    }

    @Test fun talkInterruptsTheReply() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000); m.handle(Event.ReplyAudio, 2_000)
        assertEquals(listOf(Effect.StopPlayback, Effect.Play(Earcon.Start), Effect.StartUtterance(false, byHand = true)), m.handle(Event.Talk, 3_000))
        assertEquals(Phase.Listening, m.phase)
    }

    @Test fun wakeWordIsIgnoredWhileBusyAndOff() {
        assertTrue(m.handle(Event.WakeWord, 0).isEmpty())
        assertTrue(m.handle(Event.Talk, 0).isEmpty())
        on()
        m.handle(Event.WakeWord, 0)
        assertTrue(m.handle(Event.WakeWord, 100).isEmpty())
    }

    @Test fun disableCleansUp() {
        on()
        m.handle(Event.WakeWord, 0)
        assertEquals(listOf(Effect.CancelUtterance), m.handle(Event.Disable, 100))
        assertEquals(Phase.Off, m.phase)
        on(); m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 10); m.handle(Event.ReplyAudio, 20)
        assertEquals(listOf(Effect.StopPlayback), m.handle(Event.Disable, 30))
    }

    @Test fun failureWhileTalkingBeepsAndReturns() {
        on()
        m.handle(Event.WakeWord, 0)
        assertEquals(listOf(Effect.CancelUtterance, Effect.Play(Earcon.Error)), m.handle(Event.Failure("x"), 10))
        assertEquals(Phase.Wake, m.phase)
        assertTrue(m.handle(Event.Failure("x"), 20).isEmpty())
    }

    @Test fun stuckPlaybackGivesUpAndListensForTheWakeWordAgain() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000); m.handle(Event.ReplyAudio, 2_000)
        assertTrue(m.handle(Event.Tick, 91_999).isEmpty())
        assertEquals(listOf(Effect.StopPlayback), m.handle(Event.Tick, 92_000))
        assertEquals(Phase.Wake, m.phase)
    }

    @Test fun aSoftTickWhileThinking() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000)
        assertEquals(Phase.Thinking, m.phase)
        assertEquals(emptyList<Effect>(), m.handle(Event.Tick, 2_000))
        assertEquals(listOf(Effect.Play(Earcon.Thinking)), m.handle(Event.Tick, 2_500))
        assertEquals(emptyList<Effect>(), m.handle(Event.Tick, 3_000))
        assertEquals(listOf(Effect.Play(Earcon.Thinking)), m.handle(Event.Tick, 4_000))
    }

    @Test fun stopWordEndsTheConversationAndPauseWaitsForAButton() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000)
        m.handle(Event.ReplyDone(expectReply = false, listen = false), 2_000)
        assertEquals(Phase.Wake, m.phase)  // «Стоп»: no follow-up window
        m.handle(Event.WakeWord, 3_000); m.handle(Event.SpeechEnded, 4_000)
        m.handle(Event.ReplyDone(expectReply = false, listen = false, pause = true), 5_000)
        assertEquals(Phase.Paused, m.phase)  // «Орфей, не слушать»
        m.handle(Event.WakeWord, 6_000)
        assertEquals(Phase.Paused, m.phase)  // the wake word does nothing now
        m.handle(Event.Resume, 7_000)
        assertEquals(Phase.Wake, m.phase)
        m.handle(Event.ReplyDone(false, pause = true), 8_000)
        assertEquals(Phase.Wake, m.phase)  // a stray end of a reply is not a pause
    }

    @Test fun talkButtonUnpauses() {
        on()
        m.handle(Event.WakeWord, 0); m.handle(Event.SpeechEnded, 1_000)
        m.handle(Event.ReplyDone(false, listen = false, pause = true), 2_000)
        m.handle(Event.Talk, 3_000)
        assertEquals(Phase.Listening, m.phase)
    }

    @Test fun aConversationOpenedByHandIsTheOwnersFollowUpsToo() {
        // a touch on the owner's buds: their voice was once taken for another's (0.43) and not answered
        on()
        assertEquals(listOf(Effect.Play(Earcon.Start), Effect.StartUtterance(false, byHand = true)), m.handle(Event.Talk, 1_000))
        m.handle(Event.SpeechEnded, 3_000)
        m.handle(Event.ReplyDone(false), 4_000)
        assertEquals(listOf(Effect.StartUtterance(true, byHand = true)), m.handle(Event.SpeechStarted, 5_000))
        m.handle(Event.SpeechEnded, 7_000)
        m.handle(Event.ReplyDone(false, listen = false), 8_000)
        // the next one by «Орфей»: a voice again, checked
        assertEquals(listOf(Effect.Play(Earcon.Start), Effect.StartUtterance(false)), m.handle(Event.WakeWord, 9_000))
        m.handle(Event.SpeechEnded, 11_000)
        m.handle(Event.ReplyDone(false), 12_000)
        assertEquals(listOf(Effect.StartUtterance(true)), m.handle(Event.SpeechStarted, 13_000))
    }
}
