"""Long-term memory in SQLite: facts Orpheus was told to remember, free-form notes, past conversations.

Facts are few and short, so the whole list goes into the system prompt.
Notes and conversations pile up, so they are searched on demand: by words (FTS5 over a normalized
copy of the note) and by meaning (embeddings, see vectors.py), and the best finds come along with
the phrase they answer ([recall]).
"""

import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import numpy as np

SCHEMA = """
-- small things to remember between runs: the day of the last morning summary
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS facts (
    id      INTEGER PRIMARY KEY,
    text    TEXT NOT NULL,
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY,
    title   TEXT NOT NULL DEFAULT '',
    text    TEXT NOT NULL,
    created INTEGER NOT NULL,
    updated INTEGER NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS turns (
    id      INTEGER PRIMARY KEY,
    at      INTEGER NOT NULL,
    user    TEXT NOT NULL,
    reply   TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(body, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS vectors (
    kind    TEXT NOT NULL,       -- 'fact', 'note' or 'turn'
    ref_id  INTEGER NOT NULL,
    vec     BLOB NOT NULL,       -- float32, length 1
    PRIMARY KEY (kind, ref_id)
);
"""

# A find by meaning is brought up only when it beats the question's mean similarity to a set of
# unrelated everyday sentences (vectors.ANCHORS) by this much. On the test of 13 questions: right
# finds 0.29-0.71, "привет", "спасибо" and such at most 0.31; with a single record in the memory
# 0.35 against at most 0.09. Finds by words (FTS) need no threshold.
RECALL_MARGIN = 0.33


def normalize(text):
    return text.lower().replace("ё", "е")


# Words that say nothing about what is being looked for: "какой у меня код" must find the code,
# not every note with "меня" in it.
STOP_WORDS = set("""
    а без более бы был была были было быть в вам вас весь во вот все всё всего вы где да даже для до его
    ее её если есть еще ещё же за здесь и из или им их к как какая какие каким какой какое когда кто
    ли либо мне меня мной мой моя моё мое мои мы на над надо наш не него нее неё нет ни них но ну о об
    однако он она они оно от очень по под при про с со так также такой там те тебе тебя то того тоже той
    только том ты у уже хочу чем что чтобы чье чья эта эти это этот я скажи подскажи напомни помнишь
    знаешь где-то какой-то расскажи дай покажи зовут звать зовёт называется говорил говорила говорили
    сказал сказала рассказывал рассказывала упоминал спрашивал было про
""".split())


def _match_query(query):
    """'встречи с Петей' -> 'встреч* OR пет*': a crude stem so word endings do not matter."""
    words = [w for w in re.findall(r"\w+", normalize(query)) if w not in STOP_WORDS]
    stems = [w[: max(3, len(w) - (3 if len(w) >= 7 else 2 if len(w) >= 5 else 0))] for w in words]
    return " OR ".join('"%s"*' % s for s in stems)


class Memory:
    """[embedder] (vectors.Embedder or anything with .embed(texts) -> unit rows) adds the search by
    meaning; without it only words are searched (tests, or when the model is not there)."""

    def __init__(self, path: Path | str, embedder=None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # The server answers each phrase on a fresh thread; turns never overlap (one lock), so
        # sharing the connection between threads is safe.
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.embedder = embedder
        self._matrix = None  # (keys, vectors) cached for the search by meaning


    def meta(self, key, value=None):
        """A value kept between runs: read it, or with [value] write it."""
        if value is None:
            row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        return value
    def close(self):
        self.db.close()

    # facts

    def facts(self):
        return [(r["id"], r["text"]) for r in self.db.execute("SELECT id, text FROM facts ORDER BY id")]

    def remember(self, text):
        text = text.strip()
        if not text:
            raise ValueError("пустой факт")
        now = int(time.time())
        with self.db:
            fact_id = self.db.execute("INSERT INTO facts (text, created, updated) VALUES (?, ?, ?)",
                                      (text, now, now)).lastrowid
            self.db.execute("INSERT INTO facts_fts (rowid, body) VALUES (?, ?)", (fact_id, normalize(text)))
        self._index("fact", fact_id, text)
        return fact_id

    def update_fact(self, fact_id, text):
        with self.db:
            cur = self.db.execute("UPDATE facts SET text = ?, updated = ? WHERE id = ?",
                                  (text.strip(), int(time.time()), int(fact_id)))
            if cur.rowcount:
                self.db.execute("DELETE FROM facts_fts WHERE rowid = ?", (int(fact_id),))
                self.db.execute("INSERT INTO facts_fts (rowid, body) VALUES (?, ?)", (int(fact_id), normalize(text)))
        if cur.rowcount:
            self._index("fact", int(fact_id), text.strip())
        return cur.rowcount > 0

    def forget(self, fact_id):
        with self.db:
            self.db.execute("DELETE FROM facts_fts WHERE rowid = ?", (int(fact_id),))
            self.db.execute("DELETE FROM vectors WHERE kind = 'fact' AND ref_id = ?", (int(fact_id),))
            self._matrix = None
            return self.db.execute("DELETE FROM facts WHERE id = ?", (int(fact_id),)).rowcount > 0

    # notes

    def add_note(self, text, title=""):
        text, title = text.strip(), title.strip()
        if not text:
            raise ValueError("пустая заметка")
        now = int(time.time())
        with self.db:
            note_id = self.db.execute("INSERT INTO notes (title, text, created, updated) VALUES (?, ?, ?, ?)",
                                      (title, text, now, now)).lastrowid
            self.db.execute("INSERT INTO notes_fts (rowid, body) VALUES (?, ?)",
                            (note_id, normalize(title + "\n" + text)))
        self._index("note", note_id, (title + ": " if title else "") + text)
        return note_id

    def update_note(self, note_id, text, title=None):
        cur = self.note(note_id)
        if not cur:
            return False
        title = cur["title"] if title is None else title.strip()
        with self.db:
            self.db.execute("UPDATE notes SET title = ?, text = ?, updated = ? WHERE id = ?",
                            (title, text.strip(), int(time.time()), int(note_id)))
            self.db.execute("UPDATE notes_fts SET body = ? WHERE rowid = ?",
                            (normalize(title + "\n" + text), int(note_id)))
        self._index("note", int(note_id), (title + ": " if title else "") + text.strip())
        return True

    def delete_note(self, note_id):
        with self.db:
            self.db.execute("DELETE FROM notes_fts WHERE rowid = ?", (int(note_id),))
            self.db.execute("DELETE FROM vectors WHERE kind = 'note' AND ref_id = ?", (int(note_id),))
            self._matrix = None
            return self.db.execute("DELETE FROM notes WHERE id = ?", (int(note_id),)).rowcount > 0

    def note(self, note_id):
        row = self.db.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        return dict(row) if row else None

    def recent_notes(self, limit=5):
        return [dict(r) for r in self.db.execute("SELECT * FROM notes ORDER BY updated DESC, id DESC LIMIT ?", (limit,))]

    def related_notes(self, text, limit=3):
        """Notes that share a meaningful word with [text]; empty when nothing matches."""
        if not _match_query(text or ""):
            return []
        return self.find_notes(text, limit)

    def find_notes(self, query, limit=5):
        match = _match_query(query or "")
        if not match:
            return self.recent_notes(limit)
        rows = self.db.execute(
            "SELECT notes.* FROM notes_fts JOIN notes ON notes.id = notes_fts.rowid"
            " WHERE notes_fts MATCH ? ORDER BY bm25(notes_fts) LIMIT ?", (match, limit))
        return [dict(r) for r in rows]

    # conversations

    def add_turn(self, user, reply, at=None):
        """One exchange of a conversation, kept to be found later ("что я говорил про Виктора")."""
        user, reply = user.strip(), reply.strip()
        if not user:
            return None
        at = int(at or time.time())
        with self.db:
            turn_id = self.db.execute("INSERT INTO turns (at, user, reply) VALUES (?, ?, ?)",
                                      (at, user, reply)).lastrowid
            # by words, only what the owner said: the reply would echo the question back
            self.db.execute("INSERT INTO turns_fts (rowid, body) VALUES (?, ?)", (turn_id, normalize(user)))
        self._index("turn", turn_id, "%s — %s" % (user, reply))
        return turn_id

    def turns(self, limit=20):
        return [dict(r) for r in self.db.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,))]

    # the search by meaning

    def _index(self, kind, ref_id, text):
        if self.embedder is None:
            return
        vec = self.embedder.embed([text])[0].astype(np.float32)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO vectors (kind, ref_id, vec) VALUES (?, ?, ?)",
                            (kind, int(ref_id), vec.tobytes()))
        self._matrix = None

    def reindex(self):
        """Vectors for whatever lacks them (a database from before the search by meaning)."""
        with self.db:  # word search for conversations kept before it existed
            rows = self.db.execute("SELECT id, user FROM turns WHERE id NOT IN (SELECT rowid FROM turns_fts)").fetchall()
            self.db.executemany("INSERT INTO turns_fts (rowid, body) VALUES (?, ?)",
                                [(r["id"], normalize(r["user"])) for r in rows])
            rows = self.db.execute("SELECT id, text FROM facts WHERE id NOT IN (SELECT rowid FROM facts_fts)").fetchall()
            self.db.executemany("INSERT INTO facts_fts (rowid, body) VALUES (?, ?)",
                                [(r["id"], normalize(r["text"])) for r in rows])
        if self.embedder is None:
            return 0
        missing = [("fact", r["id"], r["text"]) for r in self.db.execute(
            "SELECT id, text FROM facts WHERE id NOT IN (SELECT ref_id FROM vectors WHERE kind = 'fact')")]
        for r in self.db.execute("SELECT id, title, text FROM notes WHERE id NOT IN "
                                 "(SELECT ref_id FROM vectors WHERE kind = 'note')"):
            missing.append(("note", r["id"], (r["title"] + ": " if r["title"] else "") + r["text"]))
        for r in self.db.execute("SELECT id, user, reply FROM turns WHERE id NOT IN "
                                 "(SELECT ref_id FROM vectors WHERE kind = 'turn')"):
            missing.append(("turn", r["id"], "%s — %s" % (r["user"], r["reply"])))
        for i in range(0, len(missing), 64):
            chunk = missing[i:i + 64]
            vecs = self.embedder.embed([t for _, _, t in chunk])
            with self.db:
                self.db.executemany("INSERT OR REPLACE INTO vectors (kind, ref_id, vec) VALUES (?, ?, ?)",
                                    [(k, i_, v.astype(np.float32).tobytes()) for (k, i_, _), v in zip(chunk, vecs)])
        self._matrix = None
        return len(missing)

    def _by_words(self, table, text, limit):
        match = _match_query(text)
        if not match:
            return []
        rows = self.db.execute("SELECT rowid FROM %s WHERE %s MATCH ? ORDER BY bm25(%s) LIMIT ?" % (table, table, table),
                               (match, limit))
        return [r[0] for r in rows]

    def _vectors(self):
        if self._matrix is None:
            rows = self.db.execute("SELECT kind, ref_id, vec FROM vectors").fetchall()
            keys = [(r["kind"], r["ref_id"]) for r in rows]
            mat = np.stack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]) if rows else None
            self._matrix = (keys, mat)
        return self._matrix

    def _by_meaning(self, text, limit):
        if self.embedder is None:
            return []
        keys, mat = self._vectors()
        if mat is None:
            return []
        query = self.embedder.embed([text])[0]
        scores = mat @ query
        baseline = float((self.embedder.anchors() @ query).mean())
        return [keys[i] for i in np.argsort(-scores)[:limit] if scores[i] - baseline >= RECALL_MARGIN]

    def _item(self, kind, ref_id):
        if kind == "fact":
            row = self.db.execute("SELECT * FROM facts WHERE id = ?", (int(ref_id),)).fetchone()
            return row and {"kind": "fact", "id": row["id"], "at": row["updated"], "text": row["text"]}
        if kind == "note":
            n = self.note(ref_id)
            return n and {"kind": "note", "id": n["id"], "at": n["updated"],
                          "text": (n["title"] + ": " if n["title"] else "") + n["text"]}
        row = self.db.execute("SELECT * FROM turns WHERE id = ?", (int(ref_id),)).fetchone()
        return row and {"kind": "turn", "id": row["id"], "at": row["at"],
                        "text": "%s — %s" % (row["user"], row["reply"])}

    def recall(self, text, limit=3, facts=False):
        """Notes and past exchanges (and facts, when [facts]) related to [text]: by words first
        (exact: codes, names), then by meaning. Empty when nothing stands out."""
        if not text or not text.strip():
            return []
        keys = [("fact", f) for f in self._by_words("facts_fts", text, limit)] if facts else []
        keys += [("note", n["id"]) for n in self.related_notes(text, limit)]
        keys += [("turn", t) for t in self._by_words("turns_fts", text, limit)]
        meaning = self._by_meaning(text, limit + 3)
        keys += [k for k in meaning if k not in keys and (facts or k[0] != "fact")]
        items = [self._item(*k) for k in keys[:limit]]
        return [i for i in items if i]


def describe(item):
    """A recalled item as one line for the prompt."""
    when = datetime.fromtimestamp(item["at"]).strftime("%d.%m")
    if item["kind"] == "fact":
        return "факт: %s" % item["text"]
    if item["kind"] == "note":
        return "заметка [%d]: %s" % (item["id"], item["text"])
    return "%s говорили: %s" % (when, item["text"][:300])


class SealedMemory(Memory):
    """The same memory, kept on disk only encrypted (see secure.py): opened into RAM with the key,
    and written back, sealed, after every change."""

    WRITES = ("remember", "update_fact", "forget", "add_note", "update_note", "delete_note", "add_turn", "reindex")

    def __init__(self, path: Path | str, key: bytes, embedder=None):
        from .secure import unseal

        super().__init__(":memory:", embedder)
        self.path = Path(path)
        self.key = key
        if self.path.exists():
            self.db.deserialize(unseal(key, self.path.read_bytes()))  # WrongKey: nothing is touched
            self.db.executescript(SCHEMA)  # tables added since the file was written
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.save()

    def save(self):
        from .secure import seal, write_atomic

        write_atomic(self.path, seal(self.key, self.db.serialize()))


def _sealing(name):
    method = getattr(Memory, name)

    def wrapper(self, *args, **kwargs):
        result = method(self, *args, **kwargs)
        self.save()
        return result

    wrapper.__name__ = name
    return wrapper


for _name in SealedMemory.WRITES:
    setattr(SealedMemory, _name, _sealing(_name))
