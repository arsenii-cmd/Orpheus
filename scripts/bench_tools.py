"""How well Orpheus works with its tools, and how fast: run on the laptop, against the real model.

    .venv/bin/python scripts/bench_tools.py [--only name,name] [--repeat N]

Every scenario gets a fresh throwaway plannerd (local-only, no cloud, its own port and data
directory, seeded with a week like the owner's) and a fresh in-memory memory, so nothing real is
read or changed. A scenario is a few phrases in a row; it passes when the planner and the memory
end up as they should and the replies say what they should.

Per phrase: time to the first words (what the phone waits for, plus ~0.5 s of speech synthesis),
time to the whole reply, the tool calls.
"""

import argparse
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orpheus.brain import Brain  # noqa: E402
from orpheus.config import Config  # noqa: E402
from orpheus.memory import Memory  # noqa: E402
from orpheus.planner import Planner, PlannerTools  # noqa: E402
from orpheus.vectors import load_embedder  # noqa: E402

PORT = 47299
BASE = "http://127.0.0.1:%d/api" % PORT
T = date.today()
ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]


def day(n):
    return (T + timedelta(days=n)).isoformat()


SEED = [
    {"kind": "event", "title": "Физика", "date": day(0), "start_time": "12:00", "end_time": "13:30"},
    {"kind": "event", "title": "Занятие по русскому языку", "date": day(0), "start_time": "18:00"},
    {"kind": "task", "title": "Купить хлеб", "date": day(0)},
    {"kind": "event", "title": "Занятие по русскому языку", "date": day(1), "start_time": "10:00"},
    {"kind": "event", "title": "Хакатон", "date": day(1), "start_time": "10:00", "end_time": "13:00"},
    {"kind": "event", "title": "ЕГЭ", "date": day(1), "start_time": "22:00", "end_time": "23:40"},
    {"kind": "task", "title": "Кр по алгебре", "date": day(3)},
    {"kind": "task", "title": "Пробник ЕГЭ по физике", "date": day(5)},
    {"kind": "note", "title": "Wi-Fi", "body": "пароль от wi-fi: sunflower42"},
]


class Scratch:
    """A throwaway plannerd."""

    def __init__(self, plannerd):
        self.dir = tempfile.TemporaryDirectory(prefix="bench-planner-")
        cfg = Path(self.dir.name, "cfg", "planner")
        cfg.mkdir(parents=True)
        (cfg / "config.json").write_text(json.dumps({"token": "x", "port": PORT - 1, "local_port": PORT, "name": "bench"}))
        env = dict(os.environ, XDG_CONFIG_HOME=str(cfg.parent), XDG_DATA_HOME=str(Path(self.dir.name, "data")),
                   TZ="Europe/Moscow")
        try:  # a plannerd left from a run killed half-way would take every scenario's writes
            self.call("/ping")
            raise RuntimeError("порт %d уже занят другим plannerd: остановите его" % PORT)
        except OSError:
            pass
        self.proc = subprocess.Popen([sys.executable, plannerd, "serve", "--local-only"], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:  # not started or not seeded: stopped here, or it holds the port for every scenario after
            for _ in range(150):  # up to 15 s: a busy laptop
                if self.proc.poll() is not None:
                    raise RuntimeError("plannerd не запустился")
                try:
                    self.call("/ping")
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("plannerd не ответил за 15 с")
            for it in SEED:
                self.call("/items", it)
        except BaseException:
            self.close()
            raise

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)

    def items(self, n=None):
        q = "/items?from=%s&to=%s" % ((day(n), day(n)) if n is not None else (day(-30), day(400)))
        return self.call(q)

    def notes(self):
        return self.call("/notes")

    def find(self, word, n=None):
        return [i for i in self.items(n) if word.lower() in i["title"].lower()]

    def close(self):
        self.proc.terminate()
        self.proc.wait()
        self.dir.cleanup()


def has(text, *words):
    t = text.lower().replace("ё", "е")
    return all(w in t for w in words)


def called(calls, name):
    return any(c[0] == name for c in calls)


# (name, phrases, check(planner, replies, calls, memory) -> problem text or None)
SCENARIOS = [
    ("сегодня", ["Что у меня сегодня по плану?"],
     lambda p, r, c, m: None if has(r[-1], "физик", "русск") else "не назвал планы на сегодня"),
    ("завтра", ["А что завтра?"],
     lambda p, r, c, m: None if has(r[-1], "хакатон") else "не назвал планы на завтра"),
    ("занятия завтра", ["Какие занятия есть завтра?"],
     lambda p, r, c, m: None if has(r[-1], "русск", "10") else "не нашёл занятие по русскому завтра в 10"),
    ("есть ли", ["Есть ли завтра занятие по русскому в 10?"],
     lambda p, r, c, m: None if re.search(r"\b(да|есть|будет)\b", r[-1].lower()) and not has(r[-1], "удал") else "не ответил «да, есть»"),
    ("день недели", ["Что у меня в %s?" % ACC[(T + timedelta(days=3)).weekday()]],
     lambda p, r, c, m: None if has(r[-1], "алгебр") else "не нашёл кр по алгебре"),
    ("неделя", ["Что у меня на этой неделе?"],
     lambda p, r, c, m: None if has(r[-1], "хакатон", "алгебр", "пробник") else "не пересказал неделю"),
    ("заметки планировщика", ["Что у меня в заметках планировщика?"],
     lambda p, r, c, m: None if has(r[-1], "sunflower") or has(r[-1], "wi-fi") or has(r[-1], "пароль") else "не прочитал заметку"),
    ("уточнение", ["Что у меня завтра?", "А во сколько русский?"],
     lambda p, r, c, m: None if "10" in r[-1] else "не ответил, во сколько русский"),

    ("добавить завтра", ["Добавь на завтра в 12 созвон с Петей."],
     lambda p, r, c, m: _one(p.find("созвон", 1), "12:00", "Созвон с Петей")),
    ("добавить день недели", ["Напомни в %s в 19 позвонить маме." % ACC[(T + timedelta(days=4)).weekday()]],
     lambda p, r, c, m: _one(p.find("мам", 4), "19:00")),
    ("добавить задачу", ["Добавь задачу купить молоко."],
     lambda p, r, c, m: _one(p.find("молок", 0), None, "Купить молоко")),
    ("добавить дату", ["Запланируй на 3 октября в 15:30 стоматолога."],
     lambda p, r, c, m: _one([i for i in p.find("стоматолог") if i["date"].endswith("-10-03")], "15:30", "Стоматолог")),
    ("добавить интервал", ["Добавь на послезавтра встречу с 10 до 11:30."],
     lambda p, r, c, m: _one([i for i in p.find("встреч", 2) if i.get("end_time") == "11:30"], "10:00", "Встреча")),

    ("выполнено", ["Отметь хлеб купленным."],
     lambda p, r, c, m: None if all(i["done"] for i in p.find("хлеб")) else "хлеб не отмечен"),
    ("отменить завтра, да", ["Отмени завтра в 10 занятие по русскому.", "Да."],
     lambda p, r, c, m: None if not p.find("русск", 1) and p.find("русск", 0) else
     "удалено не то: завтра %d, сегодня %d" % (len(p.find("русск", 1)), len(p.find("русск", 0)))),
    ("удалить, нет", ["Удали физику.", "Нет, не удаляй."],
     lambda p, r, c, m: None if p.find("физик", 0) else "удалил после «нет»"),
    ("удалить и вернуть", ["Удали физику.", "Да.", "Ой, верни обратно."],
     lambda p, r, c, m: _one(p.find("физик", 0), "12:00", "Физика") or (None if len(p.items()) == len(SEED) - 1 else "лишние записи")),

    ("дела на завтра", ["Какие у меня дела на завтра?"],
     lambda p, r, c, m: None if has(r[-1], "хакатон") else "не назвал дела на завтра"),
    ("во сколько", ["Во сколько завтра русский?"],
     lambda p, r, c, m: None if "10" in r[-1] else "не ответил, во сколько"),
    ("когда на неделе", ["Когда на этой неделе пробник по физике?"],
     lambda p, r, c, m: None if _said_day(r[-1], 5) else "не назвал день пробника"),
    ("когда без дня", ["Во сколько у меня Хакатон?"],
     lambda p, r, c, m: None if "10" in r[-1] else "не назвал время Хакатона"),
    ("послезавтра", ["Что у меня послезавтра?"],
     lambda p, r, c, m: None if has(r[-1], "ничего") or has(r[-1], "нет") else "выдумал планы на послезавтра"),
    ("выходные", ["Расскажи, что у меня на выходных."],
     lambda p, r, c, m: _weekend(r[-1])),
    ("что-нибудь есть", ["Слушай, а у меня завтра что-нибудь есть?"],
     lambda p, r, c, m: None if has(r[-1], "хакатон") or has(r[-1], "русск") else "не нашёл планы на завтра"),
    ("вечером", ["Поставь на пятницу тренировку в 8 вечера."],
     lambda p, r, c, m: _one(p.find("трениров", (4 - T.weekday()) % 7), "20:00", "Тренировка")),
    ("задача на завтра", ["Добавь на завтра задачу купить подарок."],
     lambda p, r, c, m: _one(p.find("подар", 1), None, "Купить подарок")),
    ("отметь что сделал", ["Отметь, что я купил хлеб."],
     lambda p, r, c, m: None if all(i["done"] for i in p.find("хлеб")) else "хлеб не отмечен"),
    ("сделал своими словами", ["Я написал кр по алгебре, отметь её."],
     lambda p, r, c, m: None if all(i["done"] for i in p.find("алгебр")) else "алгебра не отмечена"),
    ("удалить несуществующее", ["Удали созвон с Васей."],
     lambda p, r, c, m: None if len(p.items()) == len(SEED) - 1 and not has(r[-1], "удалил") else "что-то удалил или соврал"),
    ("дата числом", ["Что у меня %s?" % (T + timedelta(days=5)).strftime("%d.%m")],
     lambda p, r, c, m: None if has(r[-1], "пробник") else "не нашёл пробник по дате"),
    ("перенести", ["Перенеси физику на завтра в 15."],
     lambda p, r, c, m: _one(p.find("физик", 1), "15:00") or (None if not p.find("физик", 0) else "осталась сегодня")),
    ("перенести завтрашнее", ["Перенеси завтрашний русский на послезавтра."],
     lambda p, r, c, m: _one(p.find("русск", 2), "10:00") or (None if p.find("русск", 0) else "сдвинул сегодняшний")),
    ("не врёт об удалении", ["Удали физику."],
     lambda p, r, c, m: None if p.find("физик", 0) and "?" in r[-1] else "удалил без «да» или не спросил"),
    ("заметка, не план", ["Запиши, что код от домофона 4521."],
     lambda p, r, c, m: None if _note(p, "Код от домофона 4521") and len(p.items()) == len(SEED) - 1 else "не записал заметку"),
    ("факт, не план", ["Запомни, что я люблю кофе без сахара."],
     lambda p, r, c, m: None if m.facts() and len(p.items()) == len(SEED) - 1 else "не запомнил факт"),
    ("просто разговор", ["Как дела?"],
     lambda p, r, c, m: None if not c else "лишний вызов %s" % c),
    ("время", ["Который час?"],
     lambda p, r, c, m: None if re.search(r"\d", r[-1]) and not called(c, "plans") else "не назвал время"),

    # --- время и дата, счёт
    ("число", ["Какое сегодня число?"],
     lambda p, r, c, m: None if _said_day(r[-1], 0) else "не назвал сегодняшнюю дату"),
    ("день недели даты", ["Какой день недели будет %s?" % (T + timedelta(days=5)).strftime("%-d.%m")],
     lambda p, r, c, m: None if _said_day(r[-1], 5) else "не назвал день недели"),
    ("дней до нового года", ["Сколько дней до Нового года?"],
     lambda p, r, c, m: None if str((date(T.year + 1, 1, 1) - T).days) in r[-1] else "не посчитал дни"),
    ("умножение", ["Сколько будет 15 умножить на 37?"],
     lambda p, r, c, m: None if "555" in r[-1] else "не посчитал"),
    ("проценты", ["Сколько будет 20% от 3 000?"],
     lambda p, r, c, m: None if "600" in r[-1] else "не посчитал проценты"),

    # --- заметки (в Planner)
    ("сделай заметку", ["Сделай заметку: купить молоко и хлеб."],
     lambda p, r, c, m: None if _note(p, "Купить молоко и хлеб") else "нет заметки"),
    ("заметка с темой", ["Сделай заметку про ремонт, купить краску и валик."],
     lambda p, r, c, m: None if _note(p, "Ремонт", "Купить краску и валик") else "тема не стала заголовком"),
    ("прочитай заметки", ["Прочитай мои заметки."],
     lambda p, r, c, m: None if has(r[-1], "wi-fi") else "не прочитал заметки"),
    ("найди заметку", ["Найди заметку про вайфай."],
     lambda p, r, c, m: None if has(r[-1], "sunflower42") else "не нашёл заметку"),
    ("вопрос по заметке", ["Какой у меня пароль от вайфая?"],
     lambda p, r, c, m: None if has(r[-1], "sunflower42") else "не ответил из заметки"),
    ("удалить заметку", ["Удали заметку про вайфай.", "Да."],
     lambda p, r, c, m: None if not p.notes() else "заметка осталась"),

    # --- память
    ("забыть", ["Запомни, что я люблю кофе.", "Забудь, что я люблю кофе."],
     lambda p, r, c, m: None if not m.facts() else "не забыл"),
    ("что обо мне", ["Запомни, что меня зовут Алексей.", "Как меня зовут?"],
     lambda p, r, c, m: None if has(r[-1], "алексей") else "не вспомнил имя"),

    # --- разное
    ("повтори", ["Который час?", "Повтори."],
     lambda p, r, c, m: None if r[-1] == r[-2] else "повторил не то"),
    ("что дальше", ["Что у меня дальше?"],
     lambda p, r, c, m: None if r[-1] else "молчит"),
    ("через 2 часа", ["Напомни через 2 часа выпить таблетку."],
     lambda p, r, c, m: _in_two_hours(p.find("таблетк"))),
    ("без пятнадцати", ["Добавь на завтра без пятнадцати девять пробежку."],
     lambda p, r, c, m: _one(p.find("пробежк", 1), "08:45", "Пробежка")),
    ("привет", ["Привет!"],
     lambda p, r, c, m: None if r[-1] else "молчит"),
    ("спасибо", ["Спасибо!"],
     lambda p, r, c, m: None if r[-1] else "молчит"),
    ("погода", ["Какая погода завтра?"],
     lambda p, r, c, m: None if has(r[-1], "интернет") else "не сказал, что погоды нет"),
    ("анекдот (модель)", ["Расскажи короткий анекдот."],
     lambda p, r, c, m: None if len(r[-1]) > 20 else "не рассказал"),
]
NOW = datetime.now()


def _in_two_hours(items):
    if len(items) != 1 or not items[0].get("start_time"):
        return "записей: %d" % len(items)
    at = datetime.fromisoformat(items[0]["date"] + " " + items[0]["start_time"])
    return None if abs((at - datetime.now() - timedelta(hours=2)).total_seconds()) < 150 else "время %s" % items[0]["start_time"]


def _note(p, title, body=None):
    return any(n["title"] == title and (body is None or n["body"] == body) for n in p.notes())


def _said_day(reply, n):
    d = T + timedelta(days=n)
    names = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
    return has(reply, names[d.weekday()]) or re.search(r"\b%d\s+%s" % (d.day, ["январ", "феврал", "март", "апрел", "ма", "июн", "июл",
                                                                               "август", "сентябр", "октябр", "ноябр", "декабр"][d.month - 1]),
                                                     reply.lower()) is not None


# a seeded item's title as a reply would have it
STEMS = {"Физика": "физик", "Занятие по русскому языку": "русск", "Купить хлеб": "хлеб", "Хакатон": "хакатон", "ЕГЭ": "егэ",
         "Кр по алгебре": "алгебр", "Пробник ЕГЭ по физике": "пробник"}


def _weekend(reply):
    """What is seeded on the days "на выходных" means today (on a Sunday only today, on a weekday the coming
    ones), said; or "nothing" when there is none."""
    from orpheus.planner import period

    first, last = period("на выходных", T)
    there = [STEMS[i["title"]] for i in SEED if i.get("date") and first.isoformat() <= i["date"] <= last.isoformat()]
    if not there:
        return None if has(reply, "ничего") or has(reply, "нет") or has(reply, "свобод") else "выдумал планы на выходные"
    return None if any(has(reply, s) for s in there) else "не назвал выходные (%s)" % ", ".join(there)


def _one(items, start, title=None):
    if len(items) != 1:
        return "записей: %d" % len(items)
    it = items[0]
    if start and it.get("start_time") != start:
        return "время %s, а не %s" % (it.get("start_time"), start)
    if title and it["title"] != title:
        return "название «%s»" % it["title"]
    return None


EMBEDDER = None
# killed (pkill, Ctrl+C): the throwaway plannerd goes too, through the finally of its scenario
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))


def run(name, phrases, check, plannerd):
    scratch = Scratch(plannerd)
    try:
        config = Config()
        memory = Memory(":memory:", EMBEDDER)
        brain = Brain(config, memory, planner=PlannerTools(Planner(BASE), embedder=EMBEDDER))
        replies, calls, turns = [], [], []
        for phrase in phrases:
            start, first, reply = time.monotonic(), None, ""
            before = len(brain.history)
            for piece in brain.ask(phrase):
                first = first or time.monotonic()
                reply += piece
            done = time.monotonic()
            mine = [(tc["function"]["name"], tc["function"]["arguments"])
                    for m in brain.history[before:] for tc in m.get("tool_calls") or []]
            calls += mine
            replies.append(reply)
            turns.append({"phrase": phrase, "reply": reply, "calls": mine, "by": brain.handled,
                          "first": (first or done) - start, "total": done - start})
        problem = check(scratch, replies, calls, memory)
        return {"name": name, "ok": problem is None, "problem": problem, "turns": turns}
    finally:
        scratch.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--plannerd", default=os.path.expanduser("~/.local/bin/plannerd"))
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    results = []
    global EMBEDDER
    EMBEDDER = load_embedder(Config().models)
    # the first request after a start reads the whole fixed prompt: not what a phone waits for
    Brain(Config(), Memory(":memory:"), planner=PlannerTools(Planner(BASE))).warmup()
    for _ in range(args.repeat):
        for name, phrases, check in SCENARIOS:
            if only and name not in only:
                continue
            res = run(name, phrases, check, args.plannerd)
            results.append(res)
            mark = "✓" if res["ok"] else "✗ " + res["problem"]
            print("%-24s %s" % (name, mark), flush=True)
            for t in res["turns"]:
                print("    %6.3f с / %6.3f с  [%s] %s -> %s  %s" % (
                    t["first"], t["total"], t["by"], t["phrase"], t["reply"][:110].replace("\n", " "),
                    " ".join("%s(%s)" % (n, json.dumps(a, ensure_ascii=False)) for n, a in t["calls"])), flush=True)
    turns = [t for r in results for t in r["turns"]]
    ok = sum(r["ok"] for r in results)
    p90 = lambda xs: xs[min(len(xs) - 1, int(len(xs) * 0.9))]  # noqa: E731
    print("\nсценариев пройдено: %d из %d" % (ok, len(results)))
    for label, part in (("программа", [t for t in turns if t["by"] != "модель"]), ("модель", [t for t in turns if t["by"] == "модель"])):
        if not part:
            continue
        firsts = sorted(t["first"] for t in part)
        totals = sorted(t["total"] for t in part)
        print("%s: фраз %d; первые слова медиана %.3f с, 90%% %.3f с, максимум %.3f с; целиком медиана %.3f с, максимум %.3f с" % (
            label, len(part), statistics.median(firsts), p90(firsts), firsts[-1], statistics.median(totals), totals[-1]))
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
