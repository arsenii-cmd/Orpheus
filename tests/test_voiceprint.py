import asyncio

import numpy as np
import pytest

from orpheus.voiceprint import ADAPT_LEASH, NEEDED, OwnerCheck, Voiceprint, unit

SR = 16000


def voice(direction, seconds=2.0):
    """A fake phrase: the 'embedding' is carried in its first samples, the rest is its length."""
    samples = np.zeros(int(seconds * SR), dtype=np.float32)
    samples[:3] = direction
    return samples


def fake_embed(samples):
    return np.array(samples[:3], dtype=np.float32)


OWNER = np.array([1.0, 0.1, 0.0])
OWNER_TIRED = np.array([0.95, 0.25, 0.1])
STRANGER = np.array([0.1, 1.0, 0.2])


def enrolled(tmp_path, mode="strict", threshold=0.5):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", mode, threshold, embed=fake_embed)
    for _ in range(NEEDED):
        assert check.enroll(voice(OWNER)) is None
    return check


def test_not_judged_until_enrolled(tmp_path):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5, embed=fake_embed)
    assert check.judge(voice(STRANGER)) == (None, True)
    assert not check.refuses(True)


def test_owner_passes_stranger_is_refused_in_strict(tmp_path):
    check = enrolled(tmp_path)
    score, owner = check.judge(voice(OWNER_TIRED))
    assert owner and score > 0.9
    score, owner = check.judge(voice(STRANGER))
    assert not owner and score < 0.5
    assert check.refuses(owner)


def test_log_mode_answers_everyone(tmp_path):
    check = enrolled(tmp_path, mode="log")
    score, owner = check.judge(voice(STRANGER))
    assert not owner and not check.refuses(owner)


def test_short_phrases_pass_unjudged(tmp_path):
    check = enrolled(tmp_path)
    assert check.judge(voice(STRANGER, seconds=0.5)) == (None, True)


def test_enrollment_rejects_short_phrases_and_survives_restart(tmp_path):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5, embed=fake_embed)
    assert "коротко" in check.enroll(voice(OWNER, seconds=0.6))
    for _ in range(NEEDED):
        check.enroll(voice(OWNER))
    again = OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5, embed=fake_embed)
    assert again.voiceprint.ready and again.voiceprint.count == NEEDED
    again.voiceprint.reset()
    assert not (tmp_path / "vp.json").exists()
    assert not OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5, embed=fake_embed).voiceprint.ready


def test_adaptation_follows_the_owner_but_stays_on_a_leash(tmp_path):
    check = enrolled(tmp_path)
    vp = check.voiceprint
    for _ in range(300):
        check.judge(voice(OWNER_TIRED))
    assert float(vp.current @ unit(OWNER_TIRED)) > float(vp.anchor @ unit(OWNER_TIRED))
    assert float(vp.current @ vp.anchor) >= ADAPT_LEASH
    # a stranger never teaches it anything
    before = vp.current.copy()
    for _ in range(50):
        check.judge(voice(STRANGER))
    assert np.allclose(before, vp.current)


def test_missing_model_turns_the_check_off(tmp_path):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5)
    assert check.mode == "off" and "нет модели" in check.problem
    assert check.judge(voice(STRANGER)) == (None, True)


def test_voiceprint_file_is_private(tmp_path):
    vp = Voiceprint(tmp_path / "vp.json")
    vp.add(OWNER)
    assert oct((tmp_path / "vp.json").stat().st_mode & 0o777) == "0o600"


# ---- the server: a stranger gets no answer, the owner does

def _server():
    """The server, for the tests below only: without websockets they are skipped, the ones above still run;
    a server that fails to import for any other reason fails them."""
    pytest.importorskip("websockets")
    from orpheus import server
    return server


class FakeRecognizer:
    def transcribe(self, samples):
        return "какая погода"


class FakeBrain:
    personal = False
    handled = "fake"

    def __init__(self):
        self.asked = []

    def ask(self, text):
        self.asked.append(text)
        yield "Солнечно."


class FakeVoice:
    def synth(self, sentence):
        return np.zeros(100, dtype=np.float32), 22050


def fake_orpheus(check):
    o = _server().Orpheus.__new__(_server().Orpheus)
    o.recognizer = FakeRecognizer()
    o.owner = check
    o.brain = FakeBrain()
    o.voices = {"male": FakeVoice()}
    o.default_voice = "male"
    o.turn_lock = asyncio.Lock()
    return o


def run_turn(o, direction, **how):
    sent = []

    async def send(message):
        sent.append(message)

    pcm = _server().to_pcm16(voice(direction))
    asyncio.run(o.turn(send, pcm, follow_up=False, **how))
    return [m for m in sent if isinstance(m, dict)]


def test_server_ignores_a_stranger_in_strict(tmp_path):
    o = fake_orpheus(enrolled(tmp_path))
    messages = run_turn(o, STRANGER)
    assert o.brain.asked == []
    assert messages == [{"type": "audio_end", "expect_reply": False, "listen": False, "reason": "not_owner"}]


def test_strict_in_the_buds_only(tmp_path):
    # the server in "log"; the phone asks for strict while it listens through the earbuds
    o = fake_orpheus(enrolled(tmp_path, mode="log"))
    assert run_turn(o, STRANGER, headset=True, strict=True)[-1].get("reason") == "not_owner"
    assert o.brain.asked == []
    assert run_turn(o, STRANGER, headset=False)[-1].get("reason") is None  # the phone's own microphone: everyone
    assert run_turn(o, OWNER, headset=True, strict=True)[-1].get("reason") is None
    assert len(o.brain.asked) == 2


def test_strict_through_the_phones_microphone_answers_everyone(tmp_path):
    # the phone asks strict only in the buds; were it asked through its own microphone, still no refusal
    o = fake_orpheus(enrolled(tmp_path, mode="log"))
    assert run_turn(o, STRANGER, headset=False, strict=True)[-1].get("reason") is None
    assert o.brain.asked == ["какая погода"]


def test_strict_asked_for_does_nothing_with_the_check_off(tmp_path):
    check = enrolled(tmp_path, mode="log")
    check.mode = "off"
    assert not check.refuses(False, strict=True)


def test_the_phones_microphone_does_not_move_the_voiceprint(tmp_path):
    check = enrolled(tmp_path, mode="log")
    before = check.voiceprint.current.copy()
    for _ in range(20):
        check.judge(voice(OWNER_TIRED), learn=False)
    assert (check.voiceprint.current == before).all()
    for _ in range(20):
        check.judge(voice(OWNER_TIRED))
    assert not (check.voiceprint.current == before).all()


def test_server_answers_the_owner(tmp_path):
    o = fake_orpheus(enrolled(tmp_path))
    messages = run_turn(o, OWNER)
    assert o.brain.asked == ["какая погода"]
    assert messages[0] == {"type": "transcript", "text": "какая погода"}
    assert messages[-1]["type"] == "audio_end" and "reason" not in messages[-1]


def test_server_enrollment_messages(tmp_path):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", "strict", 0.5, embed=fake_embed)
    o = fake_orpheus(check)
    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(o.enroll(send, _server().to_pcm16(voice(OWNER))))
    assert sent[0] == {"type": "enroll", "count": 1, "needed": NEEDED, "mode": "strict"}
    asyncio.run(o.enroll(send, _server().to_pcm16(voice(OWNER, seconds=0.5))))
    assert sent[2]["count"] == 1 and "коротко" in sent[2]["error"]
    asyncio.run(o.enroll_reset(send))
    assert sent[-1]["count"] == 0


def test_the_voiceprint_learns_only_from_the_microphone_it_was_read_into(tmp_path):
    check = OwnerCheck(tmp_path, tmp_path / "vp.json", "log", 0.5, embed=fake_embed)
    for _ in range(NEEDED):
        check.enroll(voice(OWNER), "phone")
    assert check.voiceprint.mic == "phone"
    assert check.learns("phone") and not check.learns("headset") and check.learns(None)
    again = OwnerCheck(tmp_path, tmp_path / "vp.json", "log", 0.5, embed=fake_embed)
    assert again.voiceprint.mic == "phone"  # kept in the file
    again.voiceprint.reset()
    again.enroll(voice(OWNER))  # an older app: the microphone not said, taken for the buds
    assert again.voiceprint.mic is None and again.learns("headset") and not again.learns("phone")
