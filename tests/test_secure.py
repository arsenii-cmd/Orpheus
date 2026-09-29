import pytest

from orpheus.memory import SealedMemory
from orpheus.secure import WrongKey, new_key, parse_key, seal, unseal


def test_seal_roundtrip_and_wrong_key():
    k1, k2 = parse_key(new_key()), parse_key(new_key())
    blob = seal(k1, b"secret")
    assert unseal(k1, blob) == b"secret"
    with pytest.raises(WrongKey):
        unseal(k2, blob)
    with pytest.raises(WrongKey):
        unseal(k1, b"SQLite format 3\x00...")


def test_bad_keys_are_refused():
    for bad in ["", "not base64!", "c2hvcnQ="]:
        with pytest.raises(ValueError):
            parse_key(bad)


def test_sealed_memory_is_unreadable_on_disk_and_survives_reopening(tmp_path):
    path = tmp_path / "personal.db.enc"
    key = parse_key(new_key())
    m = SealedMemory(path, key)
    m.remember("Хозяин поссорился с Виктором")
    m.add_note("Секретный код 4521")
    m.add_turn("мне грустно", "Слушаю.")
    raw = path.read_bytes()
    for plain in ["Виктор", "4521", "грустно", "SQLite format"]:
        assert plain.encode() not in raw
    again = SealedMemory(path, key)
    assert again.facts() == [(1, "Хозяин поссорился с Виктором")]
    assert again.find_notes("код")[0]["text"] == "Секретный код 4521"
    assert again.recall("что я говорил, что мне грустно")[0]["kind"] == "turn"
    assert [t for t in again.db.execute("PRAGMA integrity_check")][0][0] == "ok"


def test_a_wrong_key_leaves_the_file_alone(tmp_path):
    path = tmp_path / "personal.db.enc"
    SealedMemory(path, parse_key(new_key())).remember("x")
    before = path.read_bytes()
    with pytest.raises(WrongKey):
        SealedMemory(path, parse_key(new_key()))
    assert path.read_bytes() == before
