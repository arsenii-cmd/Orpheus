package dev.arco.orpheus

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test fun startMessage() {
        val o = JSONObject(Protocol.start(followUp = true))
        assertEquals("start", o.getString("type"))
        assertEquals(16_000, o.getInt("sample_rate"))
        assertEquals(true, o.getBoolean("follow_up"))
        assertTrue(!o.has("voice"))
        assertEquals("female", JSONObject(Protocol.start(followUp = false, voice = "female")).getString("voice"))
        assertTrue(!o.has("enroll"))
        assertEquals(true, JSONObject(Protocol.start(followUp = false, enroll = true)).getBoolean("enroll"))
        assertTrue(!o.has("headset") && !o.has("strict"))
        val buds = JSONObject(Protocol.start(followUp = false, headset = true, strict = true))
        assertEquals(true, buds.getBoolean("headset"))
        assertEquals(true, buds.getBoolean("strict"))
        assertEquals(false, JSONObject(Protocol.start(followUp = false, headset = false)).getBoolean("headset"))
        assertTrue(!o.has("id"))
        assertEquals(7, JSONObject(Protocol.start(followUp = false, id = 7)).getInt("id"))
        assertEquals("enroll_reset", JSONObject(Protocol.enrollReset()).getString("type"))
    }

    @Test fun enrollMessages() {
        assertEquals(ServerMessage.Enroll(2, 5, "strict"), Protocol.parse("""{"type":"enroll","count":2,"needed":5,"mode":"strict"}"""))
        assertEquals(
            ServerMessage.Enroll(2, 5, "log", "слишком коротко"),
            Protocol.parse("""{"type":"enroll","count":2,"needed":5,"mode":"log","error":"слишком коротко"}"""),
        )
        // a stranger's phrase: the server stays silent, the phone goes back to waiting for «Орфей»
        assertEquals(ServerMessage.AudioEnd(false, false, false), Protocol.parse("""{"type":"audio_end","expect_reply":false,"listen":false,"reason":"not_owner"}"""))
    }

    @Test fun helloCarriesThePersonalKeyOnlyWhenThereIsOne() {
        assertTrue(JSONObject(Protocol.hello("S24")).has("device"))
        assertTrue(!JSONObject(Protocol.hello("S24")).has("personal_key"))
        assertEquals("k", JSONObject(Protocol.hello("S24", " k ")).getString("personal_key"))
    }

    @Test fun modeMessage() {
        val o = JSONObject(Protocol.mode(true))
        assertEquals("mode", o.getString("type"))
        assertEquals(true, o.getBoolean("personal"))
    }

    @Test fun serverMessages() {
        assertEquals(ServerMessage.EndOfSpeech, Protocol.parse("""{"type":"end_of_speech"}"""))
        assertEquals(ServerMessage.Transcript("какая погода"), Protocol.parse("""{"type":"transcript","text":"какая погода"}"""))
        assertEquals(ServerMessage.AudioStart(24_000), Protocol.parse("""{"type":"audio","sample_rate":24000}"""))
        assertEquals(ServerMessage.AudioStart(22_050), Protocol.parse("""{"type":"audio"}"""))
        assertEquals(ServerMessage.AudioEnd(true), Protocol.parse("""{"type":"audio_end","expect_reply":true}"""))
        assertEquals(ServerMessage.AudioEnd(false, listen = false, pause = true),
            Protocol.parse("""{"type":"audio_end","expect_reply":false,"listen":false,"pause":true}"""))
        assertEquals(ServerMessage.Error("нет модели"), Protocol.parse("""{"type":"error","message":"нет модели"}"""))
        assertEquals(ServerMessage.Mode(true), Protocol.parse("""{"type":"mode","personal":true}"""))
        assertEquals(ServerMessage.Mode(false), Protocol.parse("""{"type":"mode"}"""))
        assertTrue(Protocol.parse("not json") is ServerMessage.Unknown)
        assertTrue(Protocol.parse("""{"type":"future"}""") is ServerMessage.Unknown)
    }

    @Test fun wakeWordFromVoskResult() {
        val r = """{"result":[{"conf":0.6,"end":0.7,"start":0.2,"word":"слушай"},
            {"conf":0.84,"end":1.25,"start":0.8,"word":"орфей"}],"text":"слушай орфей"}"""
        assertEquals(20_000L, findWakeWord(r, 0.75))
        assertNull(findWakeWord(r, 0.9))
        assertNull(findWakeWord("""{"result":[{"conf":1.0,"end":1.0,"start":0.5,"word":"морфей"}]}""", 0.5))
        assertNull(findWakeWord("""{"text":""}""", 0.5))
        assertTrue(WAKE_GRAMMAR.startsWith("[\"орфей\""))
        assertTrue(WAKE_GRAMMAR.endsWith("\"[unk]\"]"))
    }

    @Test fun phraseIds() {
        assertEquals(ServerMessage.AudioEnd(true, id = 3), Protocol.parse("""{"type":"audio_end","expect_reply":true,"id":3}"""))
        assertEquals(ServerMessage.Reply("да", null), Protocol.parse("""{"type":"reply","text":"да"}"""))  // an older server
    }
}
