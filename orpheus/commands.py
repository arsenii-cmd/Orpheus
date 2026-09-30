"""The owner's commands for the Planner, said in a fixed form and read by the program alone:

    Запиши <что> <когда> <повтор>        a plan; the parts in any order ("Запиши тренировку в среду в 6 утра 10 недель")
    Запиши в чек-лист <что>              a task for today, carried over by the Planner until it is done
    Заметка <текст>                      a note, word for word ("Запиши в заметки …" too)

"Запиши" must come first (after "Орфей", "так", "ну"). A plan with no time is asked "Время?", with no
repeat "Сколько раз?" (days, weeks, months, or "один раз"); the answer is waited for one phrase, and
"отмена" drops it. "Напомни мне … через …" stays the reminders' own (skills.do_plan_add).
"""

import re
from datetime import date

from .intents import FILLERS_START
from .numbers import plural, to_digits
from .planner import DAY_WORDS, MONTH_STEMS, Unavailable, _has_day, _has_time, clean_quick, spoken_day, time_span

# punctuation that is not part of a time or a date ("18:00", "25.09"), turned into spaces one for one
_PUNCT = re.compile(r"(?<!\d)[:.](?!\d)|[,!?;«»\"—–]")
RECORD = re.compile(r"запиши(?:те)?(?!\w)\s*")
CHECKLIST = re.compile(r"(?:в|во|на)\s+(?:чек\s*-?\s*лист\w*|чеклист\w*|список\s+дел)(?!\w)\s*")
TO_NOTES = re.compile(r"(?:(?:в|во)\s+заметк\w*|заметк[уа])(?!\w)\s*")
NOTE = re.compile(r"(?:новая\s+)?заметка(?!\w)\s*")
CANCEL = re.compile(r"(?<!\w)(?:отмена|отмени\w*|не\s+надо|не\s+нужно|забудь|стоп|не\s+записывай)(?!\w)")

ONCE = re.compile(r"(?<!\w)(?:(?:один|1)\s+раз|однократно|разово|единожды|без\s+повтор\w*|не\s+повтор\w*|"
                  r"повтор\w*\s+не\s+(?:надо|нужно)|не\s+надо\s+повтор\w*)(?!\w)")
UNIT_WORDS = (("day", r"каждый\s+день|ежедневно"), ("week", r"каждую\s+неделю|еженедельно|раз\s+в\s+неделю"),
              ("month", r"каждый\s+месяц|ежемесячно|раз\s+в\s+месяц"))
# "10 недель", "на 5 дней", "с повтором 3 месяца", "10 раз"; "через 2 недели" is a date
COUNT = re.compile(r"(?<!\w)(?:(?:с\s+)?повтор\w*\s+)?(?:на\s+)?(\d{1,3})\s*"
                   r"(дн(?:я|ей)|день|сут(?:ок|ки)|недел[юиья]?|нед|месяц(?:а|ев)?|мес|раз(?:а)?)(?!\w)(?:\s+подряд)?")
REPEAT_LEFT = re.compile(r"(?<!\w)(?:с\s+)?(?:повтор\w*|повторяемост\w*|повторением)(?!\w)")
DAYS_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]
DAYS_DAT = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
NO_TIME = re.compile(r"(?<!\w)(?:без\s+времени|весь\s+день|в\s+любое\s+время|когда\s+угодно|неважно|не\s+важно)(?!\w)", re.I)
PART_OF_DAY = re.compile(r"(?<!\w)(?:утром|вечером|днем|днём|ночью)(?!\w)", re.I)
LIMIT = {"day": 366, "week": 52, "month": 24}
# "к парикмахеру на субботу на 12": an hour said with "на" (not "на 3 октября")
HOUR_ON = re.compile(r"(?<!\w)на\s+(?:[01]?\d|2[0-3])(?::[0-5]\d)?(?![\w:])(?!\s+(?:%s))" % "|".join(MONTH_STEMS), re.I)


def _clean(text):
    """-> (the phrase lowered, punctuation as spaces, the phrase as said): both from the same place, the start
    ("Орфей, так, ну") cut; the same length, so a place in one is that place in the other."""
    low = _PUNCT.sub(" ", text).lower().replace("ё", "е")
    start = FILLERS_START.match(low)
    cut = start.end() if start and re.search(r"\w", low[start.end():]) else 0
    while cut < len(low) and low[cut].isspace():
        cut += 1
    return low[cut:], text[cut:]


def _unit(word):
    word = word.lower()
    if word.startswith(("дн", "день", "сут")):
        return "day"
    if word.startswith("нед"):
        return "week"
    if word.startswith("мес"):
        return "month"
    return None  # "раз": the unit said elsewhere, or a week


def read_repeat(low):
    """The repeat a phrase names -> (unit or None, count or None, weekdays or None, the phrase without it).
    count 1 is "один раз"; (None, None, None, …) is no repeat said at all. Weekdays are those of a weekly
    repeat on several days ("по понедельникам и средам", "каждый вторник и четверг", "по будням")."""
    from .skills import repeat_days, several_days
    unit = count = days = None
    t = " %s " % low
    if ONCE.search(t):
        return None, 1, None, " ".join(ONCE.sub(" ", t).split())
    for name, words in UNIT_WORDS:
        u = re.search(r"(?<!\w)(?:%s)(?!\w)" % words, t)
        if u:
            unit, t = name, t[:u.start()] + " " + t[u.end():]
            break
    if unit != "day":
        # "каждое утро" is every day in the morning
        part = re.search(r"(?<!\w)кажд\w+\s+(утро|вечер|ночь)(?!\w)", t)
        found, rest = repeat_days(t)
        if found:
            t = rest + (" %s " % {"утро": "утром", "вечер": "вечером", "ночь": "ночью"}[part.group(1)] if part else "")
            if len(found) == 7:
                unit = "day"
            else:
                unit, days = "week", found
    # "в 2 дня" is 14:00, "до 4 дня" the end, "через 2 недели" a date: not a repeat
    digits = to_digits(t)
    m = next((c for c in COUNT.finditer(digits) if c.group(0).startswith(("повтор", "с повтор", "на "))
              or not re.search(r"(?<!\w)(?:в|во|к|ко|до|с|со|по|через)\s+$", digits[:c.start()])), None)
    if m:
        n, said = int(m.group(1)), _unit(m.group(2))
        if said == "month" and unit == "week":
            n *= 4  # "по средам 2 месяца": weeks
        elif said:
            unit = said
        unit = unit or "week"
        count = n
        t = digits[:m.start()] + " " + digits[m.end():]
    if unit == "week" and days is None:
        found, rest = several_days(t)  # "в понедельник и среду в 6 утра 10 недель"
        if found and len(found) > 1:
            days, t = found, rest
    t = REPEAT_LEFT.sub(" ", t)
    return unit, count, days, " ".join(t.split())


def _how(unit, count, days):
    if count is None or count <= 1:
        return ""
    if days and len(days) > 1:
        names = [DAYS_DAT[d] for d in days]
        return ", по %s и %s, %d %s" % (", ".join(names[:-1]), names[-1], count, plural(count, "неделю", "недели", "недель"))
    every = {"day": "каждый день", "week": "каждую неделю", "month": "каждый месяц"}[unit]
    return ", %s, %d %s" % (every, count, plural(count, "раз", "раза", "раз"))


class Commands:
    """Reads the commands for [skills] (its planner, notebook, undo) and keeps the question asked last."""

    def __init__(self, skills):
        self.skills = skills
        self.draft = None  # {"turn", "text", "unit", "count", "days", "need"} - "Время?" or "Сколько раз?" asked

    def handle(self, text, now):
        """-> what to say, or None: not a command (and no answer to the question asked)."""
        s = self.skills
        low, said = _clean(text)
        draft, self.draft = self.draft, None
        if draft and draft["turn"] == s.turn - 1:
            answer = self._answer(draft, low, said, now)
            if answer is not None:
                return answer
        note = NOTE.match(low)
        if note:
            return self._note(said[note.end():])
        m = RECORD.match(low)
        if not m:
            return None
        low, said = low[m.end():], said[m.end():]
        if s.planner is None:
            return "Планировщик не подключён."
        notes = TO_NOTES.match(low)
        if notes:
            return self._note(said[notes.end():])
        check = CHECKLIST.match(low)
        if check:
            return self._checklist(said[check.end():])
        # "Запиши, что в субботу приедет мастер…": with no time, a note
        that = re.match(r"(?:то\s+)?что\s+", low)
        if that and not self._timed(low):
            return self._note(said[that.end():])
        # "запиши меня к врачу …": the words after "запиши" are the plan all the same
        unit, count, days, rest = read_repeat(low)
        # the title keeps the case it was said in: cut the same words out of the phrase as said
        rest = self._as_said(rest, said).strip(" .,!?;:")
        rest = re.sub(r"^(?:меня|мне|нам|нас)\s+", "", rest, flags=re.I)  # "запиши меня к парикмахеру"
        if not re.search(r"\w", rest):
            return "Что записать? Например: «Запиши тренировку в среду в 6 утра, 10 недель»."
        draft = {"text": rest, "unit": unit, "count": count, "days": days}
        return self._next(draft, now)

    @staticmethod
    def _as_said(rest, said):
        """[rest] (lowered, no punctuation, digits) as it was said: the case and the commas of [said], word by word."""
        key = lambda w: _PUNCT.sub("", w).lower().replace("ё", "е").strip(" .")  # noqa: E731
        words = said.split()
        out = []
        for w in rest.split():
            same = next((x for x in words if key(x) == w), None)
            out.append(same if same is not None else w)
            if same is not None:
                words = words[words.index(same) + 1:]
        return " ".join(out)

    def _timed(self, said):
        text = to_digits(said)
        return bool(_has_time(text) or time_span(text) or HOUR_ON.search(text) or PART_OF_DAY.search(text) or NO_TIME.search(text)
                    or re.search(r"(?<!\w)в\s+(?:это|то)\s+же\s+время(?!\w)", text, re.I)
                    or re.search(r"\d{1,2}:\d{2}", clean_quick(said)))  # "без пятнадцати десять", "в четверть девятого"

    def _next(self, draft, now):
        """Asks what is missing, or writes it."""
        if not self._timed(draft["text"]):
            draft.update(turn=self.skills.turn, need="time")
            self.draft = draft
            return "Время?"
        if draft["count"] is None:
            draft.update(turn=self.skills.turn, need="count")
            self.draft = draft
            return "Сколько раз?"
        return self._write(draft, now)

    def _answer(self, draft, low, said, now):
        s = self.skills
        if CANCEL.search(low):
            s.handled = "record"
            return "Хорошо, не записываю."
        if RECORD.match(low) or NOTE.match(low):
            return None  # a new command instead
        if draft["need"] == "time":
            unit, count, days, rest = read_repeat(low)
            if not self._timed(rest):
                return None
            draft["text"] = "%s %s" % (draft["text"], self._as_said(rest, said).strip(" .,!?;:"))
            if count is not None:
                draft.update(unit=unit or draft["unit"], count=count, days=days or draft["days"])
            s.handled = "record"
            return self._next(draft, now)
        unit, count, days, rest = read_repeat(low)
        if count is None:
            bare = re.fullmatch(r"\s*(?:на\s+)?(\d{1,3})\s*(?:раз\w*)?\s*", to_digits(low))
            if bare is None:
                return None
            count = int(bare.group(1))
        draft.update(unit=unit or draft["unit"] or "week", count=count, days=days or draft["days"])
        s.handled = "record"
        return self._write(draft, now)

    def _write(self, draft, now):
        s = self.skills
        planner = s.planner
        text = draft["text"]
        if NO_TIME.search(text):
            text = NO_TIME.sub(" ", text)  # a task on that day
        if not _has_time(text):
            text = PART_OF_DAY.sub(lambda p: " в %s " % {"утр": "9:00", "веч": "19:00", "ноч": "23:00"}.get(
                p.group(0)[:3], "13:00"), text, count=1)
        unit, count, days = draft["unit"] or "week", draft["count"] or 1, draft["days"]
        count = max(1, min(count, LIMIT[unit]))
        s.handled = "record"
        try:
            if days and len(days) == 1:  # "по средам": that day, every week
                text, days = "%s %s" % (DAYS_ACC[days[0]], text), None
            if not (days and len(days) > 1):
                # one plan (or several said in one phrase): the planner's own adding, with all it knows of speech
                before = planner.last_added
                answer = self._add(text, now)
                added = planner.last_added if planner.last_added is not before else None
                if added is None or count == 1:
                    return re.sub(r"^Добавил", "Записал", answer)
                s.undo_stack.pop() if s.undo_stack else None  # the series below is what "верни" takes away
                planner.repeat_item(added, unit, count)
                made = [planner.last_added]
            else:
                made = []
                for day in days:
                    answer = planner.add("%s %s" % (DAYS_ACC[day], text))
                    if planner.last_added is None:
                        return answer[:1].upper() + answer[1:] + ("" if answer.endswith(".") else ".")
                    planner.repeat_item(planner.last_added, "week", count)
                    made.append(planner.last_added)
        except Unavailable:
            return "Планировщик сейчас не отвечает."
        planner.focus = made
        self._undo(made)
        first = made[0]
        when = spoken_day(date.fromisoformat(first["date"]), planner.today(), acc=True)
        at = (" в %s" % first["start_time"]) if first.get("start_time") else ""
        return "Записал «%s»: %s%s%s." % (first["title"], when, at, _how(unit, count, days))

    def _add(self, text, now):
        """One plan, or two said with "и" ("на завтра созвон в 12 и тренировку в 18"): each its own, on the day the first names."""
        s = self.skills
        halves = re.split(r"\s+и\s+", text, maxsplit=1)
        # "в 13 обед и в 20 кино" the planner's adding splits itself
        if len(halves) == 2 and not re.match(r"(?:в|к)\s+\d", to_digits(halves[1])) and all(self._timed(h) and re.search(r"[^\W\d]{3}", re.sub(r"(?<!\w)(?:в|к|на|с|до)\s+\S+", "", h))
                                    for h in halves):
            day = re.search(DAY_WORDS, to_digits(halves[0]), re.I)
            if day and not _has_day(halves[1]):
                word = re.search(r"\S*%s\S*" % re.escape(day.group(0)), to_digits(halves[0]), re.I).group(0).strip(".,:;")
                halves[1] = "%s %s" % (word, halves[1])
            first = s.add_plan(halves[0], now)
            if s.planner.last_added is None:
                return first
            return "%s %s" % (first, re.sub(r"^Добавил", "И", s.add_plan(halves[1], now)))
        return s.add_plan(text, now)

    def _undo(self, made):
        planner = self.skills.planner.planner

        def undo():
            for i in made:
                planner.delete_series(i["series"]) if i.get("series") else planner.delete(i["id"])
            return "Убрал «%s»." % made[0]["title"]

        self.skills._can_undo(undo)

    def _checklist(self, what):
        s = self.skills
        what = " ".join(what.split()).strip(" .")
        if not re.search(r"\w", what):
            return "Что записать в чек-лист?"
        s.handled = "record"
        planner = s.planner
        try:
            item = planner.planner.save({"kind": "task", "title": what[:1].upper() + what[1:],
                                         "date": planner.today().isoformat()})
        except Unavailable:
            return "Планировщик сейчас не отвечает."
        planner.last_added = item
        planner.focus = [item]
        s._can_undo(lambda: (planner.planner.delete(item["id"]), "Убрал из чек-листа: %s." % item["title"])[1])
        return "Добавил в чек-лист: %s." % item["title"]

    def _note(self, text):
        from .skills import MemoryNotes
        s = self.skills
        text = " ".join(text.split()).strip(" .")
        if not re.search(r"\w", text):
            return "Что записать в заметку?"
        s.handled = "note_add"
        text = text[:1].upper() + text[1:]
        title, body = (text, "") if len(text) <= 60 else ("", text)
        book = s.notebook
        where = "в личные заметки" if s.brain.personal else "в заметки"
        try:
            note = book.note_add(title, body)
        except Unavailable:  # a note must not be lost: kept on the laptop until the Planner is back
            book = MemoryNotes(s.brain.active)
            note = book.note_add(title, body)
            where = "в память ноута, Planner сейчас не отвечает"
        s._can_undo(lambda: (book.note_delete(note), "Убрал заметку «%s»." % note.display)[1])
        return "Записал %s: %s." % (where, text if len(text) <= 80 else text[:77].rsplit(" ", 1)[0] + "…")
