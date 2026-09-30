"""The owner's Planner (calendar events, day tasks, notes), read and written through the plannerd
that runs on this same laptop in local-only mode: http://127.0.0.1:47211/api, no token.

That API gives full access to the Planner, so this module only ever talks to the one base address
from the config and builds every path itself: nothing the model or a phrase says becomes a URL.

Days named in words ("завтра", "пятница", "неделя") are turned into dates here, not by the model:
a 4B model gets date arithmetic wrong (see the "28 сентября" story in brain.py). Which phrase
asks for what is decided in skills.py (the templates of intents_ru.txt); the model's own tools
land here too.
"""

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

import numpy as np

from .memory import RECALL_MARGIN, STOP_WORDS
from .numbers import plural, to_digits

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]
WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
# stems that also match "в среду", "в пятницу", "в субботу", "пн"…
WEEKDAY_STEMS = [("понедельн", "пн"), ("вторн", "вт"), ("сред", "ср"), ("четверг", "чт"),
                 ("пятниц", "пт"), ("суббот", "сб"), ("воскресен", "вс")]
MONTH_STEMS = ("январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр")


class Unavailable(Exception):
    pass


def period(when, today):
    """Words -> (first day, last day), or None when they name no day."""
    w = " ".join(to_digits(when or "").lower().replace("ё", "е").split())
    one = lambda d: (d, d)  # noqa: E731
    if not w or "сегодня" in w:
        return one(today)
    if "послезавтра" in w:
        return one(today + timedelta(days=2))
    if "позавчера" in w:
        return one(today - timedelta(days=2))
    if "завтра" in w:
        return one(today + timedelta(days=1))
    if "вчера" in w:
        return one(today - timedelta(days=1))
    m = re.search(r"через\s+(\d{1,3}\s+)?(день|дня|дней|неделю|недели|недель|месяц|месяца|месяцев)(?!\w)", w)
    if m:
        n = int(m.group(1)) if m.group(1) else 1
        unit = m.group(2)
        days = n * (7 if unit.startswith("недел") else 30 if unit.startswith("месяц") else 1)
        return one(today + timedelta(days=days))
    # before "неделя": "понедельник" has it inside
    words = re.findall(r"[а-я]+", w)
    for wd, (stem, short) in enumerate(WEEKDAY_STEMS):
        if any(x.startswith(stem) or x == short for x in words):
            ahead = (wd - today.weekday()) % 7  # the same weekday: today
            if ahead == 0 and re.search(r"следующ", w):
                ahead = 7
            return one(today + timedelta(days=ahead))
    if "выходн" in w:
        sat = today + timedelta(days=(5 - today.weekday()) % 7) if today.weekday() != 6 else today
        sun = today + timedelta(days=6 - today.weekday())
        if re.search(r"следующ|будущ", w):
            sat, sun = sun + timedelta(days=6), sun + timedelta(days=7)
        return sat, sun
    if "недел" in w:
        if re.search(r"следующ|будущ", w):
            monday = today + timedelta(days=7 - today.weekday())
            return monday, monday + timedelta(days=6)
        return today, today + timedelta(days=6)
    if "месяц" in w:
        return today, today + timedelta(days=30)
    m = re.search(r"(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", w)
    if m:
        try:
            return one(_day(today, int(m.group(1)), int(m.group(2)), m.group(3)))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})\s+(" + "|".join(MONTH_STEMS) + r")[а-я]*", w)
    if m:
        month = next(i + 1 for i, s in enumerate(MONTH_STEMS) if m.group(2).startswith(s))
        try:
            return one(_day(today, int(m.group(1)), month))
        except ValueError:
            return None
    return None


def _day(today, day, month, year=None):
    if year:
        y = int(year) + (2000 if int(year) < 100 else 0)
        return date(y, month, day)
    d = date(today.year, month, day)
    return d if d >= today - timedelta(days=60) else d.replace(year=today.year + 1)


def spoken_day(d, today, acc=False):
    """"завтра, воскресенье, 27 сентября"; acc: after "на" ("на среду, 30 сентября")."""
    near = {0: "сегодня", 1: "завтра", 2: "послезавтра", -1: "вчера"}.get((d - today).days)
    text = "%s, %d %s" % ((WEEKDAYS_ACC if acc else WEEKDAYS)[d.weekday()], d.day, MONTHS[d.month - 1])
    return "%s, %s" % (near, text) if near else text


def _upper_first(text):
    return text[:1].upper() + text[1:]


def spoken_item(it):
    if it["kind"] == "task":
        return "задача «%s»%s" % (it["title"], " (сделана)" if it["done"] else "")
    time = ""
    if it.get("start_time"):
        time = "с %s до %s " % (it["start_time"], it["end_time"]) if it.get("end_time") else "в %s " % it["start_time"]
    return "%s%s%s" % (time, it["title"] or it["body"].split("\n")[0], " (отмечено)" if it["done"] else "")


@dataclass
class Note:
    """A note, the Planner's or one of the memory's ("Личное")."""
    id: object
    title: str
    body: str
    updated: float = 0
    raw: dict = field(default=None, repr=False)  # the Planner's item, to bring it back after a delete

    @property
    def display(self):
        """What a list shows, as in the Planner app: the title, or the first line of the text."""
        return self.title or self.body.split("\n")[0].strip() or "без названия"

    def spoken(self):
        if self.title and self.body:
            return "«%s»: %s" % (self.title, self.body)
        return self.title or self.body


class Planner:
    def __init__(self, base, timeout=5):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self._written = {}  # item id -> when it was last written (time.monotonic)

    def _spaced(self, item_id):
        """plannerd keeps the later of two writes to an item by their millisecond, and drops the second of two in
        the same one: the title tidied and at once the item deleted (to become a series) left it standing
        («зарядка по будням»: an extra Monday and Wednesday). Two writes to one item, 3 ms apart."""
        if not item_id:
            return
        last = self._written.get(item_id)
        if last is not None:
            wait = 0.003 - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._written[item_id] = time.monotonic()
        if len(self._written) > 500:
            self._written.clear()

    def _call(self, path, body=None, **query):
        url = self.base + path + ("?" + urlencode(query) if query else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                reason = json.load(e).get("error")
            except Exception:
                reason = e.code
            raise ValueError("планировщик ответил: %s" % reason) from None
        except OSError as e:
            raise Unavailable("планировщик недоступен (%s)" % e) from None

    def items(self, first, last, text=""):
        query = {"from": first.isoformat(), "to": last.isoformat(), "kind": "event,task"}
        if text:
            query["q"] = text
        return self._call("/items", **query)

    def notes(self):
        return self._call("/notes")

    def quick(self, text, default_day=None):
        body = {"text": text}
        if default_day:
            body["date"] = default_day.isoformat()
        item = self._call("/quick", body)["item"]
        self._written[item.get("id")] = time.monotonic()
        return item

    def save(self, item):
        self._spaced(item.get("id"))
        saved = self._call("/items", item)
        if isinstance(saved, dict) and saved.get("id") and saved.get("id") != item.get("id"):
            self._written[saved["id"]] = time.monotonic()  # a new item: its first write is now
        return saved

    def delete(self, item_id):
        self._spaced(item_id)
        return self._call("/items/%s/delete" % quote(str(item_id), safe=""), {})

    def delete_series(self, series):
        return self._call("/series/%s/delete" % quote(str(series), safe=""), {})


class PlannerTools:
    """What is done with the Planner, for the scenarios (skills.py) and for the model's tools.
    Every answer is a short Russian text: said as it is, or read by the model to phrase its reply."""

    def __init__(self, planner: Planner, today=date.today, now=datetime.now, embedder=None):
        self.planner = planner
        self.today = today
        self.now = now
        self.embedder = embedder
        self.pending_delete = None  # the model's delete_plan: (item, the turn it was proposed in, how it was said)
        self.deleted = []  # what "верни" brings back, the last one first
        self.turn = 0
        self.heard = ""
        self._note_vecs = {}  # note id -> (updated_at, vector)
        self.last_added = None  # the item add() made, for "верни как было"
        self.replaced = []  # what the last change of a repeat rewrote or deleted, as it was (for "верни")
        self.note_ids = {}  # short numbers the model's find_notes shows -> note ids
        # the items the last answer was about: "перенеси его"; and the last list read out, in the order
        # said: "удали второе", "перенеси последнее"
        self.focus = []
        self.listed = []
        self.listed_turn = -10  # when the list was read out: its items are what "перенеси русский" means for a while
        self.last_write = -1  # the turn of the last add or move: "нет, лучше на 15" moves that item again

    def new_turn(self, heard=""):
        self.turn += 1
        self.heard = heard

    # --- reading

    def about(self, text, first, last, only=False):
        """What a question is about: the items whose titles share words with it (a 4B model
        answered "пробник сегодня" from a whole week's list), else the whole list of the days."""
        today = self.today()
        items = self.planner.items(first, last)
        stems = [w[:5] for w in re.findall(r"[а-я]{3,}", re.sub(r"\w*(?:" + DAY_WORDS + r")\w*", " ", text.lower().replace("ё", "е")))
                 if w not in LISTING_WORDS and w not in STOP and w not in ASK_WORDS]
        scored = [(sum(st in _abbr((i["title"] + " " + i["body"]).lower().replace("ё", "е")) for st in stems), i) for i in items]
        best = max((sc for sc, _ in scored), default=0)
        if best:
            return "подходящее: " + "; ".join("%s — %s" % (spoken_day(date.fromisoformat(i["date"]), today), spoken_item(i))
                                            for sc, i in scored if sc == best)
        return "" if only else self.listing(first, last)

    def _question_stems(self, text):
        return [w[:5] for w in re.findall(r"[а-я]{3,}|(?<!\w)кр(?!\w)", _abbr(re.sub(r"\w*(?:" + DAY_WORDS + r")\w*", " ",
                                                              to_digits(text).lower().replace("ё", "е"))))
                if w not in LISTING_WORDS and w not in STOP and w not in ASK_WORDS]

    def matching(self, text, first, last):
        """The items a question names ("есть ли завтра русский в 10?"): those with every word of it,
        at the time named if one is. None when that is not clear-cut - the question names nothing to
        look for, or only some of its words are found ("занятие по физике" vs "Занятие по русскому"):
        then the model answers, from the list of the days."""
        stems = self._question_stems(text)
        if not stems:
            return None
        scored = [(sum(st in _abbr((i["title"] + " " + i["body"]).lower().replace("ё", "е")) for st in stems), i)
                  for i in self.planner.items(first, last)]
        found = [i for sc, i in scored if sc == len(stems)]
        _, time = self._named(text, heard=False)
        if time and found:
            found = [i for i in found if i.get("start_time") == time]
        return found or None

    def related(self, text, first, last):
        """The items sharing at least one word with the question; None when it names nothing."""
        stems = self._question_stems(text)
        if not stems:
            return None
        return [i for i in self.planner.items(first, last)
                if any(st in _abbr((i["title"] + " " + i["body"]).lower().replace("ё", "е")) for st in stems)]

    def plans(self, when, spoken=False):
        if re.search(r"заметк", when or "", re.I):
            notes = self.notes_list()
            if not notes:
                return "в планировщике нет заметок"
            return "заметки планировщика: " + "; ".join(n.spoken()[:200] for n in notes[:8])
        span = period(when, self.today())
        if span is None:
            return "уточни: не понял, какой день — «%s»" % when
        return self.listing(*span, spoken=spoken)

    def listing(self, first, last, spoken=False):
        today = self.today()
        items = self.planner.items(first, last)
        self.focus = self.listed = items
        self.listed_turn = self.turn
        if spoken and first != last and len(items) > SPOKEN_MAX:
            # "что у меня на этой неделе" read out ~1500 characters: a day a phrase, titles only, what is over left out
            now = self.now()
            ahead = [i for i in items if not i["done"] and (i["date"] > today.isoformat() or i["date"] == today.isoformat()
                                                             and (i.get("start_time") or "99") >= now.strftime("%H:%M"))]
            days = {}
            for i in ahead:
                days.setdefault(i["date"], []).append(i["title"])
            said = ["%s: %s" % (_upper_first(_near_day(date.fromisoformat(d), today)), ", ".join(t[:1].lower() + t[1:] if not t[:2].isupper() else t
                                                                                           for t in v)) for d, v in list(days.items())[:4]]
            rest = sum(len(v) for v in list(days.values())[4:])
            return ". ".join(said) + (". И ещё %d %s дальше" % (rest, plural(rest, "дело", "дела", "дел")) if rest else "")
        if first == last:
            if not items:
                return "%s: в планах ничего нет" % spoken_day(first, today)
            return "%s: %s" % (spoken_day(first, today), "; ".join(spoken_item(i) for i in items))
        if not items:
            # not "с понедельник, 5 октября по воскресенье…": no case to get wrong
            return "%s — %s: в планах ничего нет" % (spoken_day(first, today), spoken_day(last, today))
        days = {}
        for it in items:
            days.setdefault(it["date"], []).append(spoken_item(it))
        # each day a sentence of its own, capitalised: "… Завтра, понедельник, …", not "… завтра, понедельник"
        return ". ".join("%s: %s" % (_upper_first(spoken_day(date.fromisoformat(d), today)), "; ".join(v))
                         for d, v in days.items())

    def next_item(self):
        """"что у меня дальше?" - the next event today that has not started, else tomorrow's first."""
        now = self.now()
        today = now.date()
        clock = now.strftime("%H:%M")
        for day in (today, today + timedelta(days=1)):
            items = [i for i in self.planner.items(day, day) if not i["done"]]
            if day == today:
                items = [i for i in items if i.get("start_time") and i["start_time"] > clock]
            if items:
                it = items[0]
                if day == today:
                    return "Дальше %s." % spoken_item(it)
                return "Сегодня больше ничего. Завтра первым: %s." % spoken_item(it)
        return "Ни сегодня, ни завтра в планах ничего нет."

    def left_items(self):
        """Today's items by the clock: (on now, still ahead, the tasks not done). An event with no end lasts an hour."""
        now = self.now()
        today, clock = now.date(), now.strftime("%H:%M")
        items = [i for i in self.planner.items(today, today) if not i["done"]]

        def end(i):
            if i.get("end_time"):
                return i["end_time"]
            h, m = map(int, i["start_time"].split(":"))
            return "%02d:%02d" % (min(h + 1, 23), m if h < 23 else 59)
        timed = [i for i in items if i.get("start_time")]
        running = [i for i in timed if i["start_time"] <= clock < end(i)]
        ahead = [i for i in timed if i["start_time"] > clock]
        tasks = [i for i in items if not i.get("start_time")]
        self.focus = running + ahead + tasks
        return running, ahead, tasks

    def left_today(self):
        """"Что осталось на сегодня?" - by the clock: what is on now, what is still ahead, the tasks not done."""
        now = self.now()
        today, clock = now.date(), now.strftime("%H:%M")
        running, ahead, tasks = self.left_items()
        said = ["Сейчас %s." % clock]
        if running:
            said.append("Идёт: %s." % "; ".join(spoken_item(i) for i in running))
        if ahead:
            said.append("Дальше: %s." % "; ".join(spoken_item(i) for i in ahead))
        if tasks:
            said.append("Задачи на сегодня: %s." % "; ".join("«%s»" % i["title"] for i in tasks))
        if len(said) == 1:
            tomorrow = [i for i in self.planner.items(today + timedelta(days=1), today + timedelta(days=1)) if not i["done"]]
            said.append("На сегодня больше ничего." + (" Завтра первым: %s." % spoken_item(tomorrow[0]) if tomorrow else ""))
        return " ".join(said)

    # --- writing

    def add(self, text):
        # "верни как было" once came as add_plan with the words of "Удалил: …" and made an item
        # titled "Сегодня, суббота, — в Занятие…": bring back the very item instead
        self.last_added = None
        if self.deleted and RESTORE.search(self.heard):
            return self.restore()
        now = self.now()
        text = clean_quick(text, now)
        # a time that has already passed today, with no day named, is tomorrow's ("в 7" said at 20:00)
        at = re.search(r"(?<![\d.])([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
        if at and not re.search(DAY_WORDS, text, re.I) and not _has_day(self.heard) and \
                (int(at.group(1)), int(at.group(2))) < (now.hour, now.minute):
            text = "завтра " + text
        # "добавь на завтра в 12 тест" came to the tool as "в 12:00 тест": when the model drops the
        # day, the one named in the phrase itself is the default (the text's own day still wins)
        span = period(self.heard, self.today()) if re.search(r"\w", self.heard) else None
        default = span[0] if span and span[0] == span[1] and span[0] != self.today() else None
        item = self.planner.quick(text, default)
        if re.search(r"напомн", self.heard, re.I) and item.get("start_time") and item.get("remind") not in (None, 0):
            item = dict(item, remind=0)  # "напомни через 10 минут": at that time, not 15 minutes before it (already past)
            item.pop("updated_at", None)
            self.planner.save(item)
        title = tidy_title(item["title"])
        if len(re.sub(r"\W", "", title)) < 2:
            # "напомни через 20 минут" with nothing to remind of made an event «В»
            self.planner.delete(item["id"])
            return "уточни, о чём напомнить или что добавить"
        if title != item["title"]:
            item = dict(item, title=title)
            item.pop("updated_at", None)
            self.planner.save(item)
        self.last_added = item
        self.focus = [item]
        self.last_write = self.turn
        d = date.fromisoformat(item["date"])
        answer = "Добавил на %s: %s." % (spoken_day(d, self.today(), acc=True), spoken_item(item))
        if item.get("start_time") and "%sT%s" % (item["date"], item["start_time"]) < now.strftime("%Y-%m-%dT%H:%M"):
            # "напомни сегодня в пять вечера" said at 23:50: set for a time gone, without a word
            answer += " Только это время уже прошло: скажи «на завтра», если перенести"
        return answer

    def _named(self, when, heard=True):
        """The day and the time the person named: in the tool's "when", else in the phrase itself
        ("отмени завтра в 10 занятие" came to the tool as just "занятие по русскому")."""
        day = time = None
        for text in (when or "", self.heard if heard else ""):
            text = to_digits(text)
            span = period(text, self.today()) if re.search(r"\w", text) else None
            if day is None and span and span[0] == span[1]:
                day = span[0]
            m = re.search(r"(?<![\d.])([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)", text) or \
                re.search(r"(?:^|\s)(?:в|к|на)\s+([01]?\d|2[0-3])(?:\s*час\w*)?(?![\d:]|\.\d)", text, re.I)
            if time is None and m:
                time = "%02d:%s" % (int(m.group(1)), m.group(2) if m.lastindex > 1 and m.group(2) else "00")
        return day, time

    def _find(self, query, when="", heard=True, strict=False):
        """The item the words point to, on the day and at the time named if they were;
        then open ones first, then the nearest to today. [strict]: most of the words must be in its
        title (to mark done or move, with no question asked: "пробник по физике" is not "Физика")."""
        today = self.today()
        day, time = self._named(when, heard)
        first, last = (day, day) if day else (today - timedelta(days=14), today + timedelta(days=60))
        # the words of the title: not "завтрашний" of "перенеси завтрашний русский" (the day is found above)
        # "кр" and "контрольная" are one ("перенеси кр на пятницу" found nothing: two letters were no word)
        stems = [w[:5] for w in re.findall(r"\w{3,}|(?<!\w)кр(?!\w)", _abbr(query.lower().replace("ё", "е")))
                 if w not in STOP and not re.search(DAY_WORDS, w)]
        if not stems and not day and not time:
            return None, "уточни, что именно"  # "отметь к купленным": nothing to look for
        scored = []
        for it in self.planner.items(first, last):
            title = _abbr((it["title"] + " " + it["body"]).lower().replace("ё", "е"))
            score = sum(1 for st in stems if re.search(r"(?<!\w)" + re.escape(st), title))
            if score or not stems:  # (the title is _abbr'd above: "Контрольная по химии" is found by "кр" too)
                scored.append((score, it))
        if time:
            timed = [(sc, it) for sc, it in scored if it.get("start_time") == time]
            scored = timed or scored
        where = (" на " + spoken_day(day, today, acc=True)) if day else ""
        if strict and stems:
            scored = [(sc, it) for sc, it in scored if sc * 2 > len(stems)]
        if not scored:
            return None, "нет%s в планах ничего похожего на «%s»" % (where, query)
        # after "что у меня завтра?", "перенеси русский на 11" is tomorrow's (today's, already over, was moved to
        # 23:00): what was just read out comes first; then what is still ahead before what is over; then the nearest
        now = self.now()
        listed = {i["id"] for i in self.listed} if self.turn - self.listed_turn <= 3 else set()

        def over(it):
            d = date.fromisoformat(it["date"])
            return d < today or d == today and bool(it.get("start_time")) and it["start_time"] < now.strftime("%H:%M")

        def covered(it):  # how much of the title the words name: "физику" is "Физика" before "Пробник ЕГЭ по физике"
            own = [w[:5] for w in re.findall(r"\w{3,}", it["title"].lower().replace("ё", "е")) if w not in STOP]
            return sum(1 for w in own if w in stems) / len(own) if own else 0

        key = lambda x: (-x[0], x[1]["id"] not in listed, -round(covered(x[1]), 2), x[1]["done"], over(x[1]),  # noqa: E731
                         abs((date.fromisoformat(x[1]["date"]) - today).days))
        scored.sort(key=key)
        if len(scored) > 1 and key(scored[0]) == key(scored[1]):
            return None, "уточни, какое именно: " + "; ".join(
                "%s — %s" % (spoken_day(date.fromisoformat(i["date"]), today), spoken_item(i)) for _, i in scored[:3])
        return scored[0][1], None

    def describe(self, item):
        return "%s — %s" % (spoken_day(date.fromisoformat(item["date"]), self.today()), spoken_item(item))

    def done(self, query, when=""):
        item, problem = self._find(query, when, strict=True)
        if problem:
            return problem
        return self.done_item(item)

    def done_item(self, item, done=True):
        """Mark it done (or not done again: "верни" after a mark by mistake)."""
        item = dict(item, done=done)
        item.pop("updated_at", None)
        self.planner.save(item)
        self.focus = [item]
        if not done:
            return "Снял отметку: «%s» снова не сделано." % item["title"]
        today = self.today()
        d = date.fromisoformat(item["date"])
        when = "" if abs((d - today).days) <= 1 else " (%s)" % spoken_day(d, today)
        return "Отметил: «%s»%s — сделано." % (item["title"], when)

    def remove(self, item):
        """Delete an item already confirmed; it can be brought back (restore)."""
        self.planner.delete(item["id"])
        self.deleted = (self.deleted + [item])[-5:]
        return "Удалил: %s." % self.describe(item)

    def delete(self, query, when="", confirm=False):
        """The model's delete_plan. Deleting needs a clear "да" from the person in the very next
        phrase: the model once took "нет, не удаляй" for a yes, and a confirmation in the same
        turn is the model answering itself."""
        pending, self.pending_delete = self.pending_delete, None
        if pending and pending[1] == self.turn - 1:
            if NO.search(self.heard):
                return "Хорошо, не удаляю."
            if YES.search(self.heard):  # a clear yes: the model's confirm flag is not needed
                return self.remove(pending[0])
        item, problem = self._find(query, when)
        if problem:
            return problem
        what = self.describe(item)
        self.pending_delete = (item, self.turn, what)
        return "уточни: нашёл %s. Спроси, точно ли удалить; если ответят да — вызови delete_plan снова с confirm=true" % what

    def remove_all(self, items):
        """"удали всё на завтра", confirmed: every one of them; restore_all brings them back."""
        for it in items:
            self.planner.delete(it["id"])
        self.focus = []
        n = len(items)
        return "Удалил %d %s." % (n, "запись" if n % 10 == 1 and n % 100 != 11 else
                                  "записи" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "записей")

    def restore_all(self, items):
        for it in items:
            item = dict(it, deleted=False)
            item.pop("updated_at", None)
            self.planner.save(item)
        self.focus = list(items)
        if len({i["title"] for i in items}) == 1 and len(items) > 3:  # a whole repeat: not 78 times its name
            return "Вернул: «%s», %d %s." % (items[0]["title"], len(items), plural(len(items), "раз", "раза", "раз"))
        return "Вернул: %s." % "; ".join(spoken_item(i) for i in items)

    def move(self, what, to):
        """"перенеси русский с завтра на понедельник в 12": the item by the words before "на",
        the new day and time from those after it; the length of an event stays the same."""
        item, problem = self._find(_strip(what), when=what, heard=False, strict=True)
        if problem:
            return problem
        return self.move_item(item, to)

    def move_item(self, item, to, whole=True):
        """[whole]: a new time for an item of a repeat is the time of all of it ("перенеси зарядку на 7");
        False when one day of it was named ("перенеси завтрашнюю зарядку на 8")."""
        today = self.today()
        to = to_digits(to)  # "на четырнадцать", as the recogniser may write it
        said_to = to
        part = re.fullmatch(r"\s*(?:на\s+)?(утро|утром|обед|вечер|вечером|ночь|ночью)\s*", to, re.I)
        if part:  # "поменяй встречу на вечер": an hour of that part of the day
            to = "в " + PART_HOURS[part.group(1).lower()[:3]]
        elif not re.search(r"\d", to):
            # "перенеси физику на сегодня на вечер": the day and a part of it ("и так сегодня" - the part was lost)
            inner = re.search(r"(?<!\w)(?:на\s+)?(утро|утром|обед|вечер|вечером|ночь|ночью)(?!\w)", to, re.I)
            if inner:
                to = to.replace(inner.group(0), " в " + PART_HOURS[inner.group(1).lower()[:3]])
        span = time_span(to)
        if span and item["kind"] == "event":  # "сделай тренировку с 7 до 8", "перенеси тренировку на завтра с 7 до 8"
            days = period(TIME_SPAN.sub(" ", to), today) if _has_day(to) else None
            day = days[0] if days and days[0] == days[1] else None
            if item.get("series") and whole and day is None:
                return self._retime_series(item, *span)
            moved = dict(item, start_time=span[0], end_time=span[1], date=(day or date.fromisoformat(item["date"])).isoformat())
            moved.pop("updated_at", None)
            self.planner.save(moved)
            self.focus = [moved]
            self.last_write = self.turn
            return "Теперь %s: %s." % (spoken_day(date.fromisoformat(moved["date"]), today), spoken_item(moved))
        delta = shift_of(to)
        if delta is not None:  # "на час позже", "на полчаса раньше", "на день вперёд"
            return self._shift_item(item, delta)
        if re.fullmatch(r"\s*\d{1,2}(?::\d{2})?\s*", to):  # "перенеси физику на 15"
            to = "в " + to.strip()
        # "в 9" is 21:00 only when 9 in the morning is already over on the item's day, not today's clock
        # ("перенеси тренировку на 9" at 18:00 for a training on Tuesday is 09:00)
        now = self.now()
        on = date.fromisoformat(item["date"])
        to = clean_quick(to, now if on <= now.date() else datetime.combine(on, datetime.min.time()), keep_weekdays=True)
        span = period(to, today) if re.search(r"[а-я]{3}|\d[./]\d", to, re.I) else None
        _, time = self._named(to, heard=False)
        if time and item.get("start_time") and not re.search(r"утр|вечер|дня|ноч|\d:\d\d|полдень|полночь", said_to, re.I) \
                and not _has_day(said_to):  # "перенеси русский на завтра на 9": another day, the hour as said
            # "русский не в шесть а в семь" of the 18:00 lesson: 19:00, the one nearer to it (it was 07:00)
            h, mm = map(int, time.split(":"))
            old = int(item["start_time"][:2])
            if h < 12 and abs(h + 12 - old) < abs(h - old):
                time = "%02d:%02d" % (h + 12, mm)
        new_day = span[0] if span and span[0] == span[1] else date.fromisoformat(item["date"])
        moved = dict(item, date=new_day.isoformat())
        if time and item["kind"] == "event":
            if item.get("start_time") and item.get("end_time"):
                h1, m1 = map(int, item["start_time"].split(":"))
                h2, m2 = map(int, item["end_time"].split(":"))
                h, m = map(int, time.split(":"))
                end = h * 60 + m + (h2 * 60 + m2) - (h1 * 60 + m1)
                moved["end_time"] = "%02d:%02d" % (end // 60 % 24, end % 60) if end < 24 * 60 else None
            moved["start_time"] = time
        if (moved["date"], moved.get("start_time")) == (item["date"], item.get("start_time")):
            if (span and span[0] == span[1]) or time:
                # "перенеси её лучше на четверг" of a training on Thursday: asked "на какой день?" as if not heard
                self.focus = [item]
                return "«%s» и так %s: %s." % (item["title"], spoken_day(date.fromisoformat(item["date"]), today),
                                              spoken_item(item))
            return "уточни, на какой день или время перенести «%s»" % item["title"]
        if item.get("series") and whole and moved["date"] == item["date"]:
            # "перенеси зарядку на 7 утра": the time of a repeat is the time of all of it (a day moved is that day only)
            return self._retime_series(item, moved["start_time"], moved.get("end_time"))
        moved.pop("updated_at", None)
        self.planner.save(moved)
        self.focus = [moved]
        self.last_write = self.turn
        return "Перенёс на %s: %s." % (spoken_day(new_day, today, acc=True), spoken_item(moved))

    def series_items(self, item):
        """The item and the rest of its series after it (plannerd keeps each occurrence as an item)."""
        if not item.get("series"):
            return [item]
        first = date.fromisoformat(item["date"])
        return [i for i in self.planner.items(first, first + timedelta(days=800)) if i.get("series") == item.get("series")]

    def next_of(self, item):
        """The coming occurrence of the item's series (the words may have found a past one), or the item."""
        if not item.get("series"):
            return item
        today = self.today()
        ahead = [i for i in self.planner.items(today, today + timedelta(days=800)) if i.get("series") == item["series"]]
        return ahead[0] if ahead else item

    def twins(self, item):
        """"Зарядка по понедельникам, средам и пятницам" is a series for each day: the item's own and the others of
        the same title and time, each by its next occurrence."""
        if not item.get("series"):
            return [item]
        item = self.next_of(item)
        heads = {item["series"]: item}
        today = self.today()
        for i in self.planner.items(today, today + timedelta(days=7)):  # today's may be over: a week and a day
            if i.get("series") and i["title"] == item["title"] and i.get("start_time") == item.get("start_time"):
                heads.setdefault(i["series"], i)
        return sorted(heads.values(), key=lambda i: i["date"])

    def set_repeat(self, heads, unit, count):
        """Each item repeats every week or month, [count] times from it on; a series it was in ends there.
        What was replaced is in self.replaced (for "верни")."""
        made, self.replaced = [], []
        for head in heads:
            for old in self.series_items(head):
                self.replaced.append(old)
                self.planner.delete(old["id"])
            new = {k: v for k, v in head.items() if k not in ("id", "updated_at", "series", "repeat")}
            new["repeat"] = {"unit": unit, "count": count}
            made.append(self.planner.save(new))
        self.focus = made
        self.last_added = made[-1] if made else None
        self.last_write = self.turn
        return made

    def _retime_series(self, item, start, end):
        heads = self.twins(item)
        self.replaced = []
        for head in heads:
            for i in self.series_items(head):
                self.replaced.append(i)
                moved = dict(i, start_time=start, end_time=end)
                moved.pop("updated_at", None)
                self.planner.save(moved)
        self.focus = [dict(h, start_time=start, end_time=end) for h in heads]
        self.last_write = self.turn
        return "Перенёс %s все повторы «%s»." % ("на %s" % start if not end else "на время с %s до %s" % (start, end), item["title"])

    def stop_repeat(self, item):
        """The series ends with its next occurrence: that one stays, alone."""
        kept, self.replaced = [], []
        for head in self.twins(item):
            for old in self.series_items(head):
                self.replaced.append(old)
                self.planner.delete(old["id"])
            kept.append(self.planner.save({k: v for k, v in head.items() if k not in ("id", "updated_at", "series", "repeat")}))
        self.focus = kept
        self.last_write = self.turn
        return kept

    def repeat_item(self, item, unit, count):
        """The item just added made a series: every week or month, [count] times (plannerd does it)."""
        self.planner.delete(item["id"])
        new = {k: v for k, v in item.items() if k not in ("id", "updated_at", "series")}
        new["repeat"] = {"unit": unit, "count": count}
        saved = self.planner.save(new)
        self.last_added = saved
        self.focus = [saved]
        d = date.fromisoformat(saved["date"])
        return "Добавил %s, %d раз, с %d %s: %s." % ("каждую неделю" if unit == "week" else "каждый месяц", count, d.day,
                                                     MONTHS[d.month - 1], spoken_item(saved))

    def _shift_item(self, item, minutes):
        today = self.today()
        moved = dict(item)
        if item.get("start_time"):
            start = datetime.fromisoformat("%s %s" % (item["date"], item["start_time"])) + timedelta(minutes=minutes)
            moved.update(date=start.date().isoformat(), start_time=start.strftime("%H:%M"))
            if item.get("end_time"):
                end = datetime.fromisoformat("%s %s" % (item["date"], item["end_time"])) + timedelta(minutes=minutes)
                moved["end_time"] = end.strftime("%H:%M") if end.date() == start.date() else None
        elif minutes % 1440 == 0:  # a task has a day only
            moved["date"] = (date.fromisoformat(item["date"]) + timedelta(minutes=minutes)).isoformat()
        else:
            return "уточни: у «%s» нет времени, только день" % item["title"]
        moved.pop("updated_at", None)
        self.planner.save(moved)
        self.focus = [moved]
        self.last_write = self.turn
        return "Перенёс на %s: %s." % (spoken_day(date.fromisoformat(moved["date"]), today, acc=True), spoken_item(moved))

    def rename_item(self, item, title):
        new = tidy_title(_title_case(title))  # "в пробежку" -> "Пробежка"
        heads = self.twins(item)  # a repeat is renamed all of it, and its other days with it
        for head in heads:
            for i in self.series_items(head):
                renamed = dict(i, title=new)
                renamed.pop("updated_at", None)
                self.planner.save(renamed)
        self.focus = [dict(h, title=new) for h in heads]
        return "Переименовал%s: теперь это «%s»." % (" все повторы" if item.get("series") else "", new)

    def done_all(self, items, done=True):
        for it in items:
            changed = dict(it, done=done)
            changed.pop("updated_at", None)
            self.planner.save(changed)
        self.focus = list(items)

    def restore(self, query=""):
        """The item deleted last, or the last deleted one with these words ("верни русский")."""
        if not self.deleted:
            return "нет недавно удалённого, возвращать нечего"
        k = len(self.deleted) - 1
        stems = [w[:5] for w in re.findall(r"\w{3,}", (query or "").lower().replace("ё", "е")) if w not in STOP]
        if stems:
            k = next((i for i in range(len(self.deleted) - 1, -1, -1)
                      if any(st in self.deleted[i]["title"].lower().replace("ё", "е") for st in stems)), None)
            if k is None:
                return "нет среди недавно удалённого ничего похожего на «%s»" % query
        item = dict(self.deleted.pop(k), deleted=False)
        item.pop("updated_at", None)
        self.planner.save(item)
        self.focus = [item]
        return "Вернул: %s." % self.describe(item)

    # --- notes

    def notes_list(self):
        """The Planner's notes, the latest first."""
        notes = [Note(n["id"], n.get("title") or "", n.get("body") or "", n.get("updated_at") or 0, n)
                 for n in self.planner.notes()]
        return sorted(notes, key=lambda n: -n.updated)

    def note_add(self, title, body=""):
        item = self.planner.save({"kind": "note", "title": title.strip(), "body": body.strip()})
        return Note(item["id"], item.get("title") or "", item.get("body") or "", item.get("updated_at") or 0, item)

    def note_delete(self, note):
        self.planner.delete(note.id)

    def note_restore(self, note):
        item = dict(note.raw, deleted=False)
        item.pop("updated_at", None)
        self.planner.save(item)

    def note_update(self, note, body):
        """The note with another text (its title stays); -> the note as it is now."""
        item = dict(note.raw, body=body.strip(), deleted=False)
        item.pop("updated_at", None)
        saved = self.planner.save(item)
        return Note(saved["id"], saved.get("title") or "", saved.get("body") or "", saved.get("updated_at") or 0, saved)

    def notes_find(self, query, limit=3, notes=None, strict=True):
        """By words first (codes, names), then by meaning ("пароль от интернета" finds "Wi-Fi").
        strict: only finds that stand out, for the search that comes along with every phrase; an
        explicit "найди заметку про ..." takes the best one by meaning more readily."""
        notes = self.notes_list() if notes is None else notes
        if not notes or not query.strip():
            return []
        stems = _stems(query)
        by_words = []
        for n in notes:
            text = _canon(n.title + " " + n.body)
            # at a word's start: "айф" of "айфон" is not in "вайфай" (a question about an iPhone brought the
            # Wi-Fi password into the model's answer)
            hits = sum(1 for st in stems if re.search(r"(?<!\w)" + re.escape(st), text))
            if hits:
                by_words.append((hits, n))
        by_words.sort(key=lambda x: -x[0])
        found = [n for _, n in by_words]
        if self.embedder is not None:
            vecs = self._vectors(notes)
            query_vec = self.embedder.embed([query])[0]
            scores = vecs @ query_vec
            baseline = float((self.embedder.anchors() @ query_vec).mean())
            margin = RECALL_MARGIN if strict else RECALL_MARGIN * 0.6
            for i in np.argsort(-scores):
                if scores[i] - baseline < margin:
                    break
                if notes[i] not in found:
                    found.append(notes[i])
        return found[:limit]

    def _vectors(self, notes):
        missing = [n for n in notes if self._note_vecs.get(n.id, (None,))[0] != n.updated]
        if missing:
            vecs = self.embedder.embed([(n.title + ": " if n.title else "") + n.body for n in missing])
            for n, v in zip(missing, vecs):
                self._note_vecs[n.id] = (n.updated, v)
        return np.stack([self._note_vecs[n.id][1] for n in notes])

    def notes_recall(self, text, limit=2):
        """The notes a phrase is about, as lines for the prompt ("какой пароль от вайфая?")."""
        try:
            found = self.notes_find(text, limit)
        except (Unavailable, ValueError):
            return []
        return ["заметка %s" % (("«%s»: %s" % (n.title, n.body) if n.title and n.body else "«%s»" % n.display)[:300])
                for n in found]

    def tool_notes(self, query):
        """The model's find_notes: numbered, so that delete_note can name one."""
        notes = self.notes_find(query, 5) if query.strip() else self.notes_list()[:5]
        self.note_ids = {}
        lines = []
        for k, n in enumerate(notes, 1):
            self.note_ids[k] = n
            lines.append("[%d] %s" % (k, n.spoken()[:300]))
        return lines


# the same word in Latin letters and in Russian ones: "Wi-Fi" in a note, "вайфай" in what was heard
SAME_WORDS = [(r"wi-?\s?fi|вай-?\s?фай", "вайфай"), (r"whats\s?app|вотсап|вацап", "ватсап"), (r"telegram|телеграмм?", "телеграм"),
              (r"youtube|ютюб", "ютуб"), (r"iphone", "айфон"), (r"e-?mail|имейл|емейл", "почта"), (r"google|гугл", "гугл"),
              (r"zoom", "зум"), (r"skype", "скайп"), (r"pin|пин-код", "пин")]


def _abbr(text):
    """Spoken short forms as one: "контрольная", "контрольную" -> "кр"."""
    return re.sub(r"(?<!\w)контрольн\w*", "кр", text)


def _canon(text):
    text = text.lower().replace("ё", "е")
    for pattern, word in SAME_WORDS:
        text = re.sub(r"(?<!\w)(?:%s)(?!\w)" % pattern, word, text)
    return text


def _stems(text):
    """Crude stems of the words that matter: "паролем от вайфая" -> "парол", "вайф"."""
    words = [w for w in re.findall(r"\w+", _canon(text)) if w not in STOP_WORDS and len(w) > 2]
    return [w[: max(3, len(w) - (3 if len(w) >= 7 else 2 if len(w) >= 5 else 0))] for w in words]


def _say(text):
    """A tool's answer as speech: capital letter, full stop, no model-directed words."""
    text = text.strip()
    if text.startswith("уточни, какое именно: "):
        text = "Нашёл несколько: %s. Какое именно?" % text[len("уточни, какое именно: "):]
    elif text.startswith("уточни, "):
        text = text[len("уточни, "):] + "?"
    elif text.startswith("нет") and "похожего" in text:
        text = "Не нашёл " + text[len("нет"):].lstrip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


DAY_WORDS = (r"сегодня|завтра|послезавтра|вчера|позавчера|недел|выходн|месяц|через\s+\d+\s+д|\d{1,2}[./]\d{1,2}|\d{1,2}\s+(?:"
             + "|".join(MONTH_STEMS) + r")|понедельн|вторник|сред[уаы]|четверг|пятниц|суббот|воскресен")
TIME_WORDS = re.compile(r"\d{1,2}:\d{2}|(?:^|\s)(?:в|к)\s+\d{1,2}(?!\d)|через\s+(?:\d+\s+)?(?:минут|мин|час|ч\b|полчаса)|"
                        # "без пятнадцати 10", "четверть девятого", "20 минут девятого" («запиши на пятницу без
                        # пятнадцати десять стрижку» became a note)
                        r"(?<!\w)без\s+(?:четверти|\d{1,2}|пятнадцати|двадцати|десяти|пяти)\s+\d{1,2}(?!\d)|(?<!\w)четверть\s+(?:\w+ого|\d{1,2})(?!\w)|"
                        r"(?<!\w)\d{1,2}\s+минут\w*\s+(?:\w+ого|\d{1,2})(?!\w)|"
                        r"(?<!\w)(?:полчаса|половин\w+\s+\w+|пол(?:первого|второго|третьего|четвертого|пятого|шестого|седьмого|"
                        r"восьмого|девятого|десятого|одиннадцатого|двенадцатого)|полдень|полночь|утра|вечера|дня|ночи)(?!\w)", re.I)


def _has_day(text):
    return re.search(DAY_WORDS, to_digits(text), re.I) is not None


def _has_time(text):
    return TIME_WORDS.search(to_digits(text)) is not None


def _just_listing(text):
    """"а что у меня завтра?", "какие планы на неделю?" - nothing asked but the list itself."""
    rest = re.sub(r"\w*(?:" + DAY_WORDS + r")\w*|[а-я]*\d[\w.:]*", " ", to_digits(text).lower().replace("ё", "е"))
    words = re.findall(r"[а-я]+", rest)
    return all(w in LISTING_WORDS for w in words)


def _strip(what):
    """"занятие по русскому выполненным" -> the words that name the item."""
    return " ".join(ADD_FILLER.sub(" ", what).split()).strip(" .,!?")


# "какой завтра день?" is about the calendar date, not about plans
ABOUT_DATE = re.compile(r"как(ой|ое|ая)\s+(\w+\s+)?(день|число|дата|месяц|год)|который\s+час|сколько\s+(сейчас\s+)?времени|погод", re.I)
WHEN = re.compile(r"\b(когда|во\s+сколько|какого\s+числа|в\s+какой\s+день|в\s+какое\s+время)\b", re.I)
ASK_WORDS = {"когда", "сколько", "есть", "какого", "числа", "какой", "будет", "было", "там", "это", "время", "какое",
             # "во сколько заканчивается Хакатон?", "когда следующая тренировка?": words of the question, not of the title
             "заканчивается", "закончится", "кончается", "кончится", "начинается", "начнется", "следующая", "следующий",
             "следующее", "следующую", "ближайшая", "ближайший", "ближайшее", "ближайшую", "мой", "моя", "мое", "мою",
             "длится", "продлится", "идет", "займет", "длительность"}
LISTING_WORDS = {"а", "и", "ну", "еще", "что", "чё", "че", "у", "меня", "есть", "по", "плану", "планам", "планы", "план", "какие",
                 "на", "в", "во", "с", "до", "запланировано", "запланированного", "дела", "дел", "расписание", "какое",
                 "мои", "мое", "там", "будет", "будут", "нибудь", "слушай", "скажи", "сегодняшние", "завтрашние", "эту",
                 "эти", "этой", "следующей", "следующую", "следующие", "этот", "все", "всё", "расскажи", "покажи", "мне",
                 "пожалуйста", "орфей", "ли", "какой", "день", "делам", "занятия", "занятий", "задачи", "прочитай",
                 "назови", "перечисли", "напомни", "планах", "календаре", "планировщике", "расписании", "события",
                 "встречи", "мероприятия", "в", "было", "был", "была", "задач", "встреч", "событий", "мероприятий",
                 "дело"}
ADD_FILLER = re.compile(r"\b(?:мне|пожалуйста|в\s+(?:план\w*|календар\w*|планировщик\w*|список\s+дел)|на\s+(?=завтра|сегодня|послезавтра|\d)|"
                        r"задачу|задача|событие|дело|(?:как\s+)?(?:выполнен|сделан|куплен|готов|прочитан)\w*)\b", re.I)


# said around what is added, not a part of its title: "добавь задачу купить молоко", "внеси в план ..."
ADD_WORDS = re.compile(r"(?<!\w)(?:(?:мне|пожалуйста|в\s+(?:план|планы|календарь|планировщик|расписание|список\s+дел)|"
                       r"задачу|задача|событие|дело|напоминание|reminder|ремайндер|на\s+(?:его|её|ее|их)\s+место|"
                       r"вместо\s+(?:него|неё|нее|них))(?!\w)|"
                       # "на 26.09", "на 3 октября" - a date; not "на 12": an hour («Парикмахер на 12»)
                       r"на\s+(?=завтра|сегодня|послезавтра|\d{1,2}[./]\d|\d{1,2}\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|"
                       r"сентябр|октябр|ноябр|декабр)))", re.I)


def shift_of(to):
    """"час позже" -> 60, "на полчаса раньше" -> -30, "2 дня вперёд" -> 2880; None when it is no shift."""
    t = " ".join(to_digits(to).lower().replace("ё", "е").split())
    m = re.fullmatch(r"(?:на\s+)?(полчаса|полтора\s+часа|(\d+)?\s*(час\w*|минут\w*|мин|день|дн\w*|недел\w*))\s+"
                     r"(позже|раньше|вперед|назад)", t)
    if not m:
        return None
    if m.group(1) == "полчаса":
        minutes = 30
    elif m.group(1).startswith("полтора"):
        minutes = 90
    else:
        unit = m.group(3)
        per = 60 if unit.startswith("час") else 1 if unit.startswith("мин") else 10080 if unit.startswith("недел") else 1440
        minutes = int(m.group(2) or 1) * per
    return -minutes if m.group(4) in ("раньше", "назад") else minutes


def _title_case(text):
    text = " ".join(text.split()).strip(" .!?«»\"")
    return text[:1].upper() + text[1:]


# "перенеси на вечер": the hour a part of the day stands for
PART_HOURS = {"утр": "09:00", "обе": "13:00", "веч": "19:00", "ноч": "23:00"}

TIME_SPAN = re.compile(r"(?<!\w)с\s+(\d{1,2})(?::(\d{2}))?\s+до\s+(\d{1,2})(?::(\d{2}))?(?:\s*(?:час\w*\s+)?(утра|дня|вечера|ночи))?",
                       re.I)


def time_span(text):
    """"с 7 до 8" -> ("07:00", "08:00"), "с 3 до 5" -> ("15:00", "17:00") (1-6 are in the daytime, as when
    adding), "с 7 до 9 вечера" -> ("19:00", "21:00"); None when there is no such span."""
    m = TIME_SPAN.search(text)
    if not m:
        return None
    h1, m1, h2, m2, part = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0), (m.group(5) or "").lower()
    if h1 > 23 or h2 > 24:
        return None

    def hour(h):  # what "утра/дня/вечера/ночи" makes of an hour
        if part == "вечера" and h < 12 or part == "дня" and 1 <= h <= 6:
            return h + 12
        if part == "ночи" and h == 12:
            return 0
        return h

    h1, h2 = hour(h1), hour(h2)
    if (h2, m2) <= (h1, m1) and h2 < 12 and h2 + 12 > h1:  # "с 11 до 1", "с 10 до 2 дня": past noon
        h2 += 12
    if not part and 1 <= h1 <= 6 and h2 + 12 <= 24:  # "с 3 до 5": the daytime, as when adding ("с 5 до 13" stays)
        h1, h2 = h1 + 12, h2 + 12
    if (h2, m2) <= (h1, m1):
        return None
    return "%02d:%02d" % (h1, m1), "%02d:%02d" % (h2 % 24, m2)


SPOKEN_MAX = 12  # more items than this over several days are summed up when said aloud


def _near_day(d, today):
    """"сегодня", "завтра", "в четверг" - short, for a summary."""
    return {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get((d - today).days) or WEEKDAYS[d.weekday()]


def tidy_title(title):
    """"В стоматолога" -> "Стоматолог", "к врачу" -> "Врач", "Встречу" -> "Встреча": what is left of
    "запланируй на 3 октября в 15:30 стоматолога", "запиши меня к врачу на среду" or "добавь встречу"
    once the day and time are cut out (and "на" of "на среду" left hanging at the end)."""
    t = title.strip()
    # what is left of the spoken start once the day and time are cut out: "ну короче у меня в субботу в 10 пробное
    # собеседование запиши" was «Ну у меня в пробное собеседование запиши», "слушай во вторник в 15:30 у меня
    # стоматолог" «Слушай в у меня стоматолог», "напомни в следующий понедельник про кр" «Следующий про кр»
    for _ in range(3):
        t = re.sub(r"^(?:давай\s+(?:добавим|запишем|поставим|запланируем|внес[её]м)\s+)?(?:(?:ну|слушай|короче|кстати|вот|блин|ладно|так|тогда|и|а|ещё|еще|также|тоже|там|давай|следующ\w*|ближайш\w*|"
                   r"в|во|на|к|ко|про|о|об)\s+)*(?=\S)", "", t, flags=re.I)
        # "завтра в 10 у меня стоматолог", "у меня в четверг репетитор" («В репетитор»)
        t = re.sub(r"^у\s+(?:меня|нас)\s+(?=\S)", "", t, flags=re.I)
    t = re.sub(r"(?:\s+(?:запиши|записать|добавь|добавить|поставь|запланируй|внеси|отметь|пожалуйста|плиз))+$", "", t, flags=re.I)
    # "в пятницу иду на концерт": the event, not the going
    t = re.sub(r"^(?:я\s+)?(?:иду|пойду|идем|идём|пойдем|пойдём|еду|поеду|едем|поедем|схожу|сходим|собираюсь|собираемся)"
               r"(?:\s+(?:на|в|во|к|ко))?\s+(?=\S)", "", t, flags=re.I)
    t = re.sub(r"^(?:(?:в|во|на|к|ко|с|со)\s+)+(?=\S)", "", t, flags=re.I)
    t = re.sub(r"(?:\s+(?:в|во|на|к|ко|с|со|до|от|за|и|по))+$", "", t, flags=re.I)
    t = re.sub(r"(?<!\w)(в|во|на|к|с|со)\s+\1(?!\w)", r"\1", t, flags=re.I)  # "Встреча с Машей в в кафе"
    words = t.split()
    if len(words) > 1 and re.search(r"\w{2,}(?:ую|юю)$", words[0], re.I):  # "утреннюю пробежку": the adjective too
        adjective = words[0][:-2] + ("ая" if words[0][-2:].lower() == "ую" else "яя")
        rest = tidy_title(" ".join(words[1:]))
        t = "%s %s" % (adjective, rest[:1].lower() + rest[1:])
        return t[:1].upper() + t[1:]
    if len(words) == 1 and re.search(r"\w{3,}(?:ую|юю)$", words[0], re.I):  # "в контрольную": «Контрольная»
        words[0] = words[0][:-2] + ("ая" if words[0][-2:].lower() == "ую" else "яя")
    if words:
        w = words[0]
        doctor = re.fullmatch(r"(%s)(?:а|у|ом|е)?" % "|".join(DOCTORS), w, re.I)
        if doctor:
            words[0] = doctor.group(1)
        elif len(w) > 4 and re.search(r"[кчтрнлд]у$", w, re.I) and w.lower() not in VERBS_U:  # "олимпиаду"
            words[0] = w[:-1] + "а"
        elif re.search(r"ию$", w, re.I):
            words[0] = w[:-1] + "я"
    t = " ".join(words)
    return t[:1].upper() + t[1:]


# the people one goes to: "к врачу", "записаться к стоматологу" -> "Врач", "Стоматолог"
DOCTORS = ["врач", "стоматолог", "терапевт", "окулист", "офтальмолог", "лор", "дантист", "парикмахер", "юрист",
           "нотариус", "психолог", "психотерапевт", "кардиолог", "невролог", "хирург", "гинеколог", "уролог",
           "дерматолог", "педиатр", "ортодонт", "массажист", "косметолог", "репетитор", "ветеринар", "эндокринолог",
           "гастроэнтеролог", "травматолог", "ортопед", "логопед", "мастер", "тренер", "барбер", "доктор"]


VERBS_U = {"хочу", "лечу", "плачу", "верну", "начну", "сверну", "кручу", "учу", "шучу", "звучу"}

# "в половине восьмого": the hour in the genitive of an ordinal
HOURS_GEN = {"первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "пятого": 5, "шестого": 6, "седьмого": 7,
             "восьмого": 8, "девятого": 9, "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12}
QUARTERS = {"четверти": 15, "пятнадцати": 15, "десяти": 10, "пяти": 5, "двадцати": 20, "двадцати пяти": 25}


def clean_quick(text, now=None, keep_weekdays=False):
    """A phrase for quick-add, tidied, with the times it does not know turned into "HH:MM":
    "в половине восьмого", "без пятнадцати восемь", "в 8 вечера", "через 2 часа", "в полдень".
    An hour said without "утра"/"вечера" is taken as the owner means it: 1-6 are in the daytime
    ("в 3" is 15:00), and today's hour that has already passed is the evening one ("в 7" at 17:00
    is 19:00). And "Сегодня, суббота, — в 18:00 занятие" is "сегодня в 18:00 занятие": a weekday next
    to a date would end up in the title."""
    now = now or datetime.now()
    t = re.sub(r"\s[—–]\s|^[—–]\s|[,;«»\"]", " ", text)
    t = t.replace("ё", "е").replace("Ё", "Е")
    # "на завтра в 10, нет, в 11 встречу": the time said last («Нет в 11:00 встречу», at 10)
    t = re.sub(r"(?<!\w)(?:в|к|на)\s+\S+(?:\s+(?:утра|дня|вечера|ночи))?\s+(?:нет|вернее|верней|точнее|то\s+есть|ой)\s+(?=(?:в|к|на)\s)",
               "", t, flags=re.I)

    def half(m):
        hour = HOURS_GEN[m.group(1).lower()] - 1
        return "в %d:30" % (hour or 12)

    t = re.sub(r"(?<!\w)(?:в\s+)?(?:половине|пол)\s*(" + "|".join(HOURS_GEN) + r")(?!\w)", half, t, flags=re.I)

    def before(m):
        minutes = QUARTERS.get(" ".join(m.group(1).lower().split())) or int(m.group(1))
        hour = int(to_digits(m.group(2)))
        return "в %d:%02d" % ((hour - 1) or 12, 60 - minutes)

    t = re.sub(r"(?<!\w)(?:в\s+)?без\s+(четверти|двадцати\s+пяти|двадцати|пятнадцати|десяти|пяти|\d{1,2})\s+(\w+)(?!\w)",
               lambda m: before(m) if to_digits(m.group(2)).isdigit() and 1 <= int(to_digits(m.group(2))) <= 12 else m.group(0),
               t, flags=re.I)
    # "в четверть девятого" (8:15), "в двадцать минут девятого" (8:20)
    t = re.sub(r"(?<!\w)(?:в\s+)?четверть\s+(" + "|".join(HOURS_GEN) + r")(?!\w)",
               lambda m: "в %d:15" % ((HOURS_GEN[m.group(1).lower()] - 1) or 12), t, flags=re.I)
    t = re.sub(r"(?<!\w)(?:в\s+)?(\S+(?:\s+\S+)?)\s+минут\w*\s+(" + "|".join(HOURS_GEN) + r")(?!\w)",
               lambda m: ("в %d:%02d" % ((HOURS_GEN[m.group(2).lower()] - 1) or 12, int(to_digits(m.group(1))))
                          if to_digits(m.group(1)).isdigit() and 0 < int(to_digits(m.group(1))) < 60 else m.group(0)), t, flags=re.I)
    t = re.sub(r"(?<!\w)(?:в\s+)?полдень(?!\w)", "в 12:00", t, flags=re.I)
    t = re.sub(r"(?<!\w)(?:в\s+)?полночь(?!\w)", "в 00:00", t, flags=re.I)
    t = to_digits(t)

    # "на 15-е", "15-го", "к 3 числу": that day of this month, or of the next one when it has passed
    def of_month(m):
        day = int(m.group(1))
        month, year = now.month, now.year
        if day < now.day:
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
        try:
            return " %d %s " % (date(year, month, day).day, MONTHS[month - 1])  # in words: "01.10" was read as 01:10
        except ValueError:
            return m.group(0)

    t = re.sub(r"(?<!\w)(?:на\s+|к\s+)?(\d{1,2})\s*-?\s*(?:е|го|ое|ого)(?:\s+числ[оау])?(?!\w)|(?<!\w)(?:на\s+|к\s+)?(\d{1,2})\s+числ[оау](?!\w)",
               lambda m: of_month(re.match(r"(\d+)", m.group(1) or m.group(2))), t, flags=re.I)
    # "через 3 дня", "через 2 недели": a date ("3 дня" was read as 15:00)
    t = re.sub(r"(?<!\w)через\s+(\d{1,2})\s+(дн\w*|день|недел\w*)(?!\w)",
               lambda m: (lambda d: " %d %s " % (d.day, MONTHS[d.month - 1]))(
                   now + timedelta(days=int(m.group(1)) * (7 if m.group(2).startswith("недел") else 1))),
               t, flags=re.I)
    # "через неделю в это же время": now's time
    t = re.sub(r"(?<!\w)в\s+это\s+же\s+время(?!\w)", lambda m: "в %s" % now.strftime("%H:%M"), t, flags=re.I)

    # "через 2 часа", "через 15 мин", "через полчаса": from now
    def later(m):
        if m.group(1) and m.group(1).startswith("полчас"):
            minutes = 30
        elif m.group(1) and m.group(1).startswith("полтор"):
            minutes = 90
        else:
            n = int(m.group(2) or 1)
            minutes = n * 60 if m.group(3).lower().startswith("ч") else n
        at = now + timedelta(minutes=minutes)
        return ("в %s" % at.strftime("%H:%M")) if at.date() == now.date() else at.strftime("%d.%m в %H:%M")

    t = re.sub(r"(?<!\w)через\s+(?:(полчаса|полтора\s+часа)|(\d{1,3})?\s*(часов|часа|час|ч|минут[уы]?|мин))(?!\w)",
               later, t, flags=re.I)

    # "в 7 30 утра" (the recogniser's "в семь тридцать утра": «в 07:00 30:00 зарядку»): an hour and its minutes
    t = re.sub(r"(?<!\w)(в|к)\s+([01]?\d|2[0-3])\s+([0-5]\d)(?!\s*(?:\d|минут|мин|час|раз|недел|дн|человек|%|рубл|процент))",
               r"\1 \2:\3", t, flags=re.I)
    marked = r"\s*(?:час(?:а|ов)?|ч\.?)?\s+(утра|дня|вечера|ночи)(?!\w)"

    def with_part(m):
        h, mi, part = int(m.group(1)), m.group(2) or "00", m.group(3).lower()
        if part in ("дня", "вечера") and h < 12:
            h += 12
        elif part == "ночи" and h == 12:
            h = 0
        return "%02d:%s" % (h, mi)  # "06:00": a leading zero keeps it from the daytime guess below

    t = re.sub(r"(?<![\d:])(\d{1,2})(?::([0-5]\d))?" + marked, with_part, t, flags=re.I)
    t = re.sub(r"(?<!\w)в\s+(\d{1,2})\s*(?:часов|часа|час|ч\.?)(?!\w)", lambda m: "в %s:00" % int(m.group(1)), t, flags=re.I)
    # "к парикмахеру на субботу на 12" (a task «Парикмахер на 12»): "на" + an hour, when no other time is
    # named and nothing after it makes it a count ("на 2 недели", "на 5 человек", "на 3 октября")
    if not re.search(r"(?<![\d.])\d{1,2}:\d{2}|(?:^|\s)(?:в|к)\s+\d", t):
        t = re.sub(r"(?<!\w)на\s+(\d{1,2})(?::([0-5]\d))?(?=\s*(?:[.,!?]|$|\s+(?:утра|дня|вечера|ночи)(?!\w)))",
                   lambda m: "в %s:%s" % (m.group(1), m.group(2) or "00") if int(m.group(1)) <= 23 else m.group(0), t, flags=re.I)

    names_day = re.search(r"сегодня|завтра|послезавтра|\d{1,2}[./]\d{1,2}|\d{1,2}\s+(" + "|".join(MONTH_STEMS) + ")|"
                          r"понедельн|вторник|сред[уа]|четверг|пятниц|суббот|воскресен|через\s+\d+\s+(?:дн|недел)", t, re.I)

    def daytime(m):
        h, mi = int(m.group(2)), m.group(3) or "00"
        if 1 <= h <= 6:
            h += 12
        elif 7 <= h <= 11 and (not names_day or re.search(r"сегодня", t, re.I)) and \
                (h, int(mi)) <= (now.hour, now.minute) < (h + 12, int(mi)):
            h += 12
        return "%s %d:%s" % (m.group(1), h, mi)

    t = re.sub(r"(?<!\w)(в|к)\s+([1-9]|1[01])(?::([0-5]\d))?(?![\d:]|\s*(?:утра|дня|вечера|ночи))", daytime, t, flags=re.I)
    if not keep_weekdays and re.search(r"сегодня|завтра|\d{1,2}[./]\d{1,2}|\d{1,2}\s+(" + "|".join(MONTH_STEMS) + ")", t, re.I):
        t = re.sub(r"(?:^|\s)(?:во?\s+)?(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье)(?=\s|$)",
                   " ", t, flags=re.I)
    return " ".join(t.split()).strip(" .")


RESTORE = re.compile(r"\b(верни|вернуть|восстанови|отмени\s+удаление|как\s+было|назад\b|обратно)", re.I)
STOP = {"мое", "мой", "моя", "про", "это", "эту", "этот", "тот", "все", "всё", "дело", "задачу", "событие"}
# "За." is how a short "да" is often heard; only a whole answer, not "за что?"
YES = re.compile(r"^\W*(да|ага|угу|конечно|точно|подтверждаю|удаляй|давай)\b|^\W*(удали|за)\W*$", re.I)
NO = re.compile(r"\b(нет|не\s+(надо|нужно|удаляй|удалять|трогай)|отмена|оставь|стой|подожди)\b", re.I)


def from_config(config, embedder=None):
    """The planner tools, or None when the Planner is turned off ("-")."""
    if not config.planner or config.planner == "-":
        return None
    return PlannerTools(Planner(config.planner), embedder=embedder)
