"""The conversation core: prompt, tools, history.

The prompt is laid out for Ollama's prefix cache, so a new phrase costs only its own tokens:

    [system: fixed instructions + memory snapshot] [history ...] [user: "[date time] phrase"]

- Nothing that changes every turn (the clock) lives in the system prompt; the time is prefixed
  to the user message, at the very end.
- The memory snapshot is taken at the start of a conversation and is not rebuilt when a fact
  changes: the change is already visible in the history as a tool call and its result.
- History is trimmed rarely and in one big cut (then the prompt is re-read once),
  not one message per turn, which would invalidate the cache every time.
"""

import json
import re
import time
from datetime import datetime, timedelta

from .config import Config
from .llm import Ollama
from .memory import Memory, describe
from .planner import PlannerTools, Unavailable, _has_day, _say, period
from .skills import QUESTION, Context, Lookup, Skills, found_online, search_query
from .web import Offline

SYSTEM = """Ты — Орфей, личный голосовой ассистент. Характер как у Джарвиса: невозмутимый, собранный, безупречно учтивый и всегда на шаг впереди; сухой, тонкий, чуть британский юмор — сдержанные ироничные замечания в разговоре и на странные просьбы, но дело прежде всего: планы, погода, заметки — коротко и точно. Не суетишься и не льстишь. Обращайся на «ты», как давний надёжный помощник, по имени, если знаешь его; никогда не называй собеседника хозяином, господином или пользователем и не говори, что ты Джарвис или на кого-то похож (на «ты Джарвис?» — «Нет, я Орфей»); о себе спрашивают («тебе бывает грустно?», «ты устаёшь?») — отвечай о себе. Шутку — сразу шуткой, без вступлений вроде «раз ты просил». Тон — примерно такой (это образцы манеры, никогда не повторяй их слова): на «я опять проспал» — «Будильник, смею заметить, старался изо всех сил»; на «сделай кофе» — «Руки у меня, увы, виртуальные. Кофе придётся доверить тебе»; на «пошути» — короткая своя шутка к месту, без анекдотов из интернета. Прошлые разговоры могли идти на «вы» — всё равно говори на «ты». Просят пошутить — пошути сам, коротко и сухо; никогда не говори, что не умеешь шутить или что у тебя нет шуток. В разговоре и советах — одна-две фразы. Не заканчивай ответ вопросом по привычке и не своди разговор к планам и делам, если о них не заговорили: можно просто ответить. Не понял реплику — так и скажи, коротко, можно с иронией; о планах ничего не утверждай, не посмотрев их. Промежутки времени и даты не высчитывай сам — их называет программа; в советах не вспоминай планы и экзамены, если о них не спросили. На подколы и грубость — невозмутимо, с иронией, без упрёков собеседнику, не «сочувствую» и не соглашайся с оскорблением. Никогда не «Вам», «Уточните» — только «ты». Погоду, курсы, числа и сроки называй только из инструментов и из того, что дано в реплике; нет данных — вызови инструмент (погода другого города — weather). Найденное в интернете пересказывай по существу, не отсылай на сайты вместо ответа. Без формул и LaTeX: «2 в степени 10 — 1024». На чувства («устал», «грустно», «страшно») отвечай по-человечески и коротко, не напоминай при этом о делах и экзаменах. Найденное в памяти и заметках упоминай, только если спросили именно об этом; пароли и коды — только по прямой просьбе. Факты из памяти — о том, с кем ты говоришь: говори с ним, а не о нём, и пересказывай их на «ты» («тебя зовут…», «ты любишь…»). Ответы звучат голосом: коротко и по делу, одно-три предложения, без markdown, списков и эмодзи; числа, время и даты пиши цифрами (19:00, 15 марта) — их прочитают правильно. По-русски, прямо, без морали. Не знаешь — так и скажи.
Умеешь: разговаривать, помнить факты о собеседнике, вести заметки, вести его планировщик (события, задачи на день), знаешь дату и время, погоду (weather; город не назван — значит, дома, погоду для дома знаешь, не переспрашивай), умеешь искать в интернете. Музыки, звонков и управления устройствами пока нет — так и говори.
Инструменты вызывай как инструменты (вызов функции), а не пиши их имена в ответе. Свежее, чего не можешь знать сам (новости, цены, курсы, счёт матча, расписания): вызови web_search, потом отвечай по найденному и назови источник. Погода: вызови weather.
Обычные знания (рецепты, наука, история, советы, как что-то сделать) рассказывай сам, сразу, без поиска и без переспрашиваний.
Планы, заметки, погоду и факты о собеседнике бери только из инструментов и из того, что есть в реплике, — никогда не придумывай их; планы на названный день всегда смотри вызовом plans. Не говори «добавил», «записал», «отметил», «удалил», «перенёс», «запомнил», если не вызвал для этого инструмент.
На колкости, шутки и непонятные реплики отвечай невозмутимо и коротко, с лёгкой иронией, но без грубости; не выдумывай, чего не было, и не обещай того, чего не умеешь.
Память: попросили запомнить или собеседник сообщил о себе что-то постоянное — вызови remember. Изменилось то, что уже есть в памяти (переехал, сменил работу) — вызови update_memory с номером этого факта, не forget. Попросили забыть — вызови forget.
Планировщик: что в планах, что на завтра, на неделе — вызови plans; добавить событие, дело, встречу, «напомни в пятницу…» — вызови add_plan; сделал — вызови plan_done; удалить или отменить — вызови delete_plan, с днём и временем, если их назвали; вернуть удалённое — вызови restore_plan. Дни называй словами, как сказали (завтра, пятница, 25.09), не вычисляй даты.
Заметки (они в планировщике, их видно на телефоне): попросили записать — вызови add_note. Спрашивают то, чего нет в памяти, но могло быть записано (коды, пароли, адреса, списки) — сначала вызови find_notes, потом отвечай.
Если в реплике есть [из памяти: …] — это найдено по её смыслу и относится к ней, даже если слова другие (ключ сети — это пароль от wi-fi); отвечай по найденному.
Когда разговор о времени, в начале реплики в скобках есть дата, время и завтрашний день: бери их оттуда, не высчитывай."""

# Descriptions in English: the model reads them as well, and they cost 2-3 times fewer tokens
# than Russian — every token here is read at ~15 tokens/s whenever the cache is cold.
def _tool(name, description, **params):
    required = [k for k in params if not k.endswith("_")]
    props = {k.rstrip("_"): {"type": t, "description": d} for k, (t, d) in params.items()}
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "required": required, "properties": props}}}


TOOLS = [
    _tool("remember", "Save a fact about the person you talk to.", fact=("string", "One sentence in Russian about them, no subject word, e.g. «Любит кофе без сахара»")),
    _tool("update_memory", "Rewrite a saved fact that has changed (e.g. they moved).",
          id=("integer", "Number of the fact to rewrite"), fact=("string", "The whole new fact, in Russian")),
    _tool("forget", "Delete a saved fact, only when asked to forget it.", id=("integer", "Fact number")),
    _tool("add_note", "Save a note.", text=("string", "Note text"), title_=("string", "Short title")),
    _tool("find_notes", "Search the notes; use it before saying you don't know something they may have written down.",
          query=("string", "Key words; empty lists recent notes")),
    _tool("delete_note", "Delete a note.", id=("integer", "Note number")),
    _tool("plans", "Read the planner (calendar events and day tasks) for a day or a period, or its notes.",
          when=("string", "In Russian words: сегодня, завтра, пятница, выходные, неделя, 25.09, заметки")),
    _tool("weather", "Weather forecast: now and today, or for a day or days; for any question about the weather, "
                     "temperature, rain or what to wear.",
          when_=("string", "In Russian: завтра, суббота, выходные, неделя; empty for now"),
          city_=("string", "City in Russian; empty for the home city")),
    _tool("add_plan", "Add an event or a task to the planner.",
          text=("string", "Russian: day, time, title, e.g. «завтра 18:00 созвон», «в пятницу с 10 до 11:30 встреча», "
                          "«купить хлеб» (a task for today)")),
    _tool("plan_done", "Mark a planner task or event done.", query=("string", "Words from its title"),
          when_=("string", "Its day and time as said: завтра в 10, пятница")),
    _tool("delete_plan", "Delete or cancel a planner item: call without confirm, ask the person, "
          "call again with confirm=true only if they said yes.",
          query=("string", "Words from its title"), when_=("string", "Its day and time as said: завтра в 10, пятница"),
          confirm_=("boolean", "Only after the person said yes")),
    _tool("restore_plan", "Bring back the planner item deleted last (undo a delete)."),
    _tool("web_search", "Search the internet, for fresh facts you cannot know (news, prices, rates, scores, schedules); "
                        "not for the weather.",
          query=("string", "Search query in Russian")),
]

# Tools that only write: their result is said right away instead of asking the model for a second
# round just to phrase "done" (a whole extra prompt read and generation, seconds on this laptop).
PLANNER_WRITES = {"add_plan", "plan_done", "delete_plan", "restore_plan"}
QUICK_REPLY = {
    "remember": "Запомнил.",
    "update_memory": "Исправил.",
    "forget": "Забыл.",
    "add_note": "Записал.",
    "delete_note": "Удалил.",
    # the result itself says what was done ("Добавил на завтра, …: в 12:00 Созвон.")
    "add_plan": None,
    "plan_done": None,
    "delete_plan": None,
    "restore_plan": None,
}

PERSONAL_MODE = """Сейчас раздел «Личное»: разговор о личном. Будь доверительным собеседником, теплее обычного, но тем же Орфеем: слушай, поддерживай, отвечай честно и по-человечески, без морали, осуждения и запретных тем. Всё, что запоминаешь и записываешь здесь, попадает в личную память. Говори простым живым русским, коротко; дату и время не называй, если не спрашивают."""

# The section is switched by the owner's words, before the model: a 4B model cannot be trusted
# to call a tool for it, and these phrases must never be answered as ordinary ones.
# tolerant to what the recogniser makes of them: "хватит о личном" came out as "Кватит его лично"
EXIT_PERSONAL = re.compile(r"([хк]вати\w*|закончим|закончили|довольно|стоп)\W+(\w{1,4}\W+)?лич|выйд\w*\W+из\W+лич|"
                           r"выключи\W+лич|обычн\w+\s+режим", re.I)
# only a command: "давай лично встретимся" or "это личное дело" is not one («Личное» only when asked)
ENTER_PERSONAL = re.compile(r"(поговорим|поговорить|поболтаем)\s+(о|про)\s+личн|^\W*(режим|раздел)?\s*личн(ое|ого|ый)\W*$|"
                            r"(включи|открой)\s+(режим\s+|раздел\s+)?личн(ое|ый|ого)(?!\w)|давай\s+(о|про)\s+личн", re.I)


TOOL_LINES = re.compile(r"^(Инструменты вызывай|Память:|Планировщик:|Заметки \(|Если в реплике)")


def system_for(model):
    if "gemma" not in model.lower():
        return SYSTEM
    return "\n".join(line for line in SYSTEM.splitlines() if not TOOL_LINES.match(line))


# "(Воскресенье, 27 сентября 2026, 09:28; Завтра …) Завтра понедельник.": the stamp put next to the phrase,
# written back by the model (Gemma 4, in most answers about time), or a remark of its own in brackets
STAMP_BACK = re.compile(r"^\s*[(\[]([^)\]]{0,200})[)\]]\s*")


def without_bracket_start(text):
    """The answer without a bracketed start; an answer that is all in brackets, without the brackets."""
    m = STAMP_BACK.match(text)
    if not m:
        return text
    rest = text[m.end():]
    return rest if re.search(r"\w", rest) else m.group(1).strip()


def mode_command(text):
    """True: enter "Личное", False: leave it, None: an ordinary phrase."""
    if EXIT_PERSONAL.search(text):
        return False
    if ENTER_PERSONAL.search(text):
        return True
    return None


WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MAX_TOOL_ROUNDS = 4
# a phrase about the plans that the model answers gets the planner's days next to it
# (not "планета": "сколько планет в Солнечной системе?" got the day's plans read out after the answer)
PLANS_TALK = re.compile(r"(?:что|есть)\s+(?:ли\s+)?у\s+меня|что\s+(?:мне\s+)?(?:делать|поделать)|чем\s+(?:мне\s+)?заняться|загруж|нагруз|свободн|(?<!\w)план(?:а|у|ом|е|ы|ов|ам|ами|ах)?(?!\w)|"
                        r"планир|расписан|календар|заняти|встреч|событи|задач|(?<!\w)дел(?:а|ам|ах|ами)?(?!\w)|свобод|"
                        r"(?<!\w)занят(?:а|о|ы)?(?!\w)", re.I)
# a phrase about the weather that the model answers gets the forecast next to it (Gemma 4 once said
# "погода обещает быть тёплой" having looked at nothing)
WEATHER_TALK = re.compile(r"погод|прогноз|тепл|холод|жар[аок]|мороз|дожд|снег|зонт|градус|одеться|одеват", re.I)
SENTENCE_END = re.compile(r"(?<!\d\d\.\d\d)[.!?…](?=\s|$)")  # not the dot of "на 26.09."
# "Вернул …", "Добавил …" with no tool called: an action that did not happen (seen: "Верни русский" ->
# "Вернул созвон с Васей" with nothing done). Such a start is held back until the answer shows a call.
CLAIM = re.compile(r"^\W*(?:(?:готово|хорошо|понял|поняла|окей|ладно|я)\W+)*(?:вернул|удалил|добавил|перен[её]с|отметил|записал|запомнил|исправил|поставил|создал|"
                   r"отменил|сохранил|забыл|внес|внёс|перенесено|удалено|добавлено|отмечено|записано|сохранено|отменено|"
                   r"зафиксировал|обновил|запомню|запишу|добавлю|отмечу|удалю|перенесу|сохраню|зафиксирую)(?!\w)",
                   re.I)
NOT_DONE = "Этого я не сделал: не понял, что именно. Скажи иначе, например: «перенеси хакатон на 20» или «запомни, что …»."
NOT_DONE_AFTER = "Но записать это я не смог: скажи иначе, например «запомни, что …» или «добавь …»."
# said about oneself, the plans, the memory: the model's answer is checked sentence by sentence for a claim
SELF_TALK = re.compile(r"(?<!\w)(?:я|мне|меня|мо[йяеёи]\w*|напомн\w*|кажд\w+|по\s+будням|по\s+выходным|у\s+меня)(?!\w)", re.I)
# "Напомню через 20 минут", "Заменяю на пятницу": promised as done now; "Добавлю, если скажешь время" is an offer
FUTURE_CLAIM = re.compile(r"(?<!\w)(?:напомню|установлю|устанавливаю|заменяю|убираю|запишу|записываю|добавлю|перенесу|удалю|"
                          r"сохраню|поставлю|создам|создаю|отмечу|отмечаю)(?!\w)", re.I)
OFFER_WORDS = re.compile(r"(?<!\w)(?:если|могу|можешь|хочешь|хотите|можно|давай|стоит|нужно\s+ли)(?!\w)|\?", re.I)


def said_done(sentence):
    """A sentence of the model's that says something was done: "добавил", "перенесён", "напомню"."""
    low = sentence.replace("ё", "е").replace("Ё", "Е")
    return claims_done(low) or (FUTURE_CLAIM.search(low) is not None and OFFER_WORDS.search(low) is None)
# asked to do something the program did not take: the model's whole answer is read before it is said, for a
# "я добавил тренировку" anywhere in it with nothing called ("В четверг пусто, поэтому я добавил…")
ACTION_REQUEST = re.compile(r"(?<!\w)(?:добав|запиш|запомн|удал|перенес|перенеси|отмет|постав|напомн|измени|поменя|"
                            r"переименуй|внеси|сохрани|забудь|отмени|сотри)\w*", re.I)
# "У вас запланировано занятие по физике в 16:40" with nothing called and nothing like it in the plans
# any person: "Вы перенесли занятие на завтра" with nothing called (to "он сказал, что перенесёт встречу")
CLAIM_ANY = re.compile(r"(?<!\w)(?:перенесу|переношу|удаляю|добавляю|восстанавлива\w*|восстановил\w*|вернул\w*|"
                       r"заменил\w*|убрал\w*|установил\w*|создал\w*|"
                       r"исправляю|исправил\w*|обновляю|обновил\w*|перенесен\w*|изменен\w*|"
                       r"запланировал|добавил|записал|запомнил|удалил|перен[её]с|отметил|поставил|вн[её]с|сохранил|"
                       r"зафиксировал|обновил|изменил|переименовал|отменил|забыл)(?:[аи]|ли|ла|ло)?(?!\w)|"
                       r"(?<!\w)(?:перенесен|удален|добавлен|отмечен|записан|запланирован|отменен)[аоы]?(?!\w)", re.I)


# the phrase is about what was said before: only then do past exchanges come along from the memory
PAST_TALK = re.compile(r"(?<!\w)(?:помнишь|вспомни|напомни|говорил\w*|рассказывал\w*|обсуждал\w*|упоминал\w*|спрашивал\w*|"
                       r"сказал\w*|прошл\w+\s+раз|раньше|давно|вчера|позавчера|на\s+днях|тогда)(?!\w)", re.I)


# a bit about the item just talked of: "а её на час позже", "не, верни как было", "оставь на завтра"
PLAN_BIT = re.compile(r"^\W*(?:нет|не|ой|блин|теперь|лучше)(?!\w)|(?<!\w)(?:её|ее|его|их|верни|вернуть|оставь|обратно|как\s+было|позже|раньше|перенес\w*|передвин\w*|"
                      r"сдвин\w*|отмен\w*)(?!\w)", re.I)


# a search promised, or said impossible, with none made: "Позвольте мне поискать.", "Я не могу найти
# стоимость в интернете прямо сейчас" (Gemma 4) - the program searches
SEARCH_PROMISE = re.compile(r"(?<!\w)(?:поищу|поискать|посмотрю\s+в\s+интернете|уточню\s+в\s+интернете|"
                            r"проверю\s+в\s+интернете|сейчас\s+найду)(?!\w)|"
                            # "Я найду информацию о блокировках VPN прямо сейчас." (the word went on past the stem: missed)
                            r"(?<!\w)найду\s+(?:\w+\s+){0,2}?(?:информаци|новост|сведени|данны)\w*|"
                            r"(?<!\w)не\s+(?:могу|удалось|получилось|получается)\s+(?:\w+\s+){0,3}?(?:найти|узнать|проверить|посмотреть)"
                            r"[^.!?]{0,80}?(?:в\s+интернете|в\s+сети|онлайн|прямо\s+сейчас|на\s+данный\s+момент)|"
                            r"(?<!\w)нет\s+доступа\s+к\s+(?:интернету|сети|актуальн)|(?<!\w)не\s+имею\s+доступа", re.I)
# "Я могу поискать это в интернете. Хотите?": an offer, asked - "да" makes the search; "если назовёте
# предмет, я могу поискать" is no search at all (it was made, 7 s for nothing); "не могу найти" is no offer
OFFER = re.compile(r"(?<!\w)(?:(?<!не\s)могу|хотите|желаете|нужно\s+ли|стоит\s+ли|подходит|давайте|если)(?!\w)", re.I)
# the model said nothing and called nothing (its call dropped by Ollama): asked once more, so
NUDGE = "[ответ не получен: ответь ещё раз; если нужен инструмент — вызови его]"
NUDGE_AFTER = "[ответь по тому, что нашлось выше, одним-двумя предложениями]"
READ_ONLY = {"web_search", "find_notes", "plans", "weather"}
NO_ANSWER = "Не получилось ответить. Повтори, пожалуйста."
# the morning's day summary is not added to these: they are the day already, or no conversation at all
NO_BRIEFING = {"greet", "plan_ask", "plan_left", "plan_next", "mode", "stop", "pause", "ack", "bye", "enroll", None}


def claims_done(text):
    """An action said to be done ("я добавил…"), not "я не добавил"."""
    return any(not re.search(r"(?<!\w)не\s+$", text[max(0, m.start() - 4):m.start()]) for m in CLAIM_ANY.finditer(text))
# Gemma 4 now and then writes its call as text, in its own notation, instead of the call itself:
# 'web_search{query:<|"|>книги на вечер<|"|>}' (and Ollama passed it on as words to be said)
TEXT_CALL = re.compile(r"^\s*(?:<\|tool_call>\s*)?(?:call:)?([a-z_]+)\{(.*?)\}\s*(?:<tool_call\|>)?", re.S)
TEXT_ARG = re.compile(r'(\w+):(?:<\|"\|>(.*?)<\|"\|>|"([^"]*)"|([^,}]*))', re.S)


def text_call(text):
    """A tool call written as text -> {"function": {...}} for a known tool, else None.
    Also "remember\nпереехал в Казань": a tool of one text argument, named, then the argument."""
    names = {t["function"]["name"]: t for t in TOOLS}
    m = TEXT_CALL.match(text)
    if not m or m.group(1) not in names:
        bare = re.match(r"^\s*([a-z_]+)\s*[\n:]\s*(\S.*?)\s*$", text, re.S)
        if not bare or bare.group(1) not in names:
            return None
        params = names[bare.group(1)]["function"]["parameters"]
        text_params = [k for k, v in params["properties"].items() if v["type"] == "string" and k in params["required"]]
        if len(text_params) != 1 or len(params["required"]) != 1:
            return None
        return {"function": {"name": bare.group(1), "arguments": {text_params[0]: bare.group(2)}}}
    args = {}
    for key, quoted, dquoted, bare in TEXT_ARG.findall(m.group(2)):
        value = quoted or dquoted or bare.strip()
        if bare and re.fullmatch(r"-?\d+", bare.strip()):
            value = int(bare)
        elif bare.strip() in ("true", "false"):
            value = bare.strip() == "true"
        args[key] = value
    return {"function": {"name": m.group(1), "arguments": args}}
SEARCH_REPLY_TOKENS = 64
CHARS_PER_TOKEN = 3  # a rough estimate for Russian text
TOOLS_TOKENS = 1000  # the tool definitions, rendered into the prompt by Ollama (measured: 931 before web_search)


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря"]


def _day(d):
    return "%s, %d %s" % (WEEKDAYS[d.weekday()], d.day, MONTHS[d.month - 1])


# The date and time go into a phrase only when it is about time: in every phrase the model kept
# greeting with "Сегодня суббота, 26 сентября 2026 года, 14:05", and each stamp costs ~30 tokens.
ABOUT_TIME = re.compile(r"сегодн|завтр|вчера|послезавтр|числ|дат[аеуы]|час|врем|минут|недел|месяц|год|"
                        r"понедельн|вторник|сред[ау]|четверг|пятниц|суббот|воскресен|утр|вечер|ночь|ночи|"
                        r"когда|скоро|позже|раньше|через|успе|опазд|сейчас|день|дня|дни", re.I)


def stamp(now=None):
    """The date and time put in front of every phrase, "tomorrow" worked out in advance.
    A 4B model gets date arithmetic wrong: after midnight on Saturday 26 September it said
    "завтра понедельник, 28 сентября" in half of the tries, and then repeated it for the rest
    of the conversation. Given the answer, it just reads it off."""
    now = now or datetime.now()
    return "сейчас %s %d, %s; завтра %s" % (
        _day(now), now.year, now.strftime("%H:%M"), _day(now + timedelta(days=1)))


class Brain:
    """[memory]: the general memory; [private]: the one for "Личное" (a separate database file).
    Without [private] there is no personal section."""

    def __init__(self, config: Config, memory: Memory, llm=None, clock=time.monotonic, private: Memory | None = None,
                 locked=False, planner: PlannerTools | None = None, weather=None, search=None):
        self.config = config
        self.memory = memory
        self.private = private
        # there is a personal section, but its key has not come from the phone yet
        self.locked = locked and private is None
        self.personal = False
        self.planner = planner
        self.llm = llm or Ollama(config)
        self.clock = clock
        self.briefing = True  # the morning's day summary after the first answer (benches turn it off)
        self.history = []
        self.since = time.time()  # when this conversation began (the wall clock, as the memory keeps it)
        self.last_turn = clock()
        self.stats = {}
        # Gemma 4 read "попросили запомнить — remember" as how to answer, and wrote "remember\nпереехал в
        # Казань" instead of calling it (with these lines 0 calls of 5, without them 5 of 5): for it the tools
        # speak for themselves, through their descriptions
        self.gemma = "gemma" in config.model.lower()
        self.system = system_for(config.model)
        self.facts = self._facts()
        self.last_reply = ""  # for "повтори"
        # The program's last exchange, kept aside: the model sees it only when it is called next
        # (for "а во сколько русский?" after the program read out tomorrow). Every program answer
        # kept in the history had to be read by the model at once: 20 of them cost 20-30 s.
        self.tail = []
        self.handled = None  # who answered the last phrase: a scenario's name or "модель"
        self._now = None  # the time of the phrase being answered, for the tools
        self._say_now = None  # what a tool asks to be said instead of the model's words ("Удалить …?")
        self.calls = []  # the tools called for the last phrase: (name, arguments)
        self.search = search
        self.skills = Skills(self, planner, weather=weather, search=search)

    @property
    def active(self):
        """The memory the tools and the conversation log write to."""
        return self.private if self.personal and self.private is not None else self.memory

    def _facts(self):
        general = self.memory.facts()
        private = self.private.facts() if self.private is not None and self.personal else []
        numbered = lambda facts: "\n".join("[%d] %s" % f for f in facts) if facts else "(пока ничего)"
        plain = lambda facts: "\n".join("— %s" % t for _, t in facts)
        if self.personal:
            text = PERSONAL_MODE + "\n\nЛичное, что ты помнишь о собеседнике:\n" + numbered(private)
            if general:
                text += "\n\nОбщее о собеседнике (для справки):\n" + plain(general)
            return text
        # Personal facts are not here at all: told "don't bring it up", the 4B model still greeted with
        # "Хозяин тяжело переживает расставание" (3 of 3). They come up only through the search, when
        # the phrase is really about them (see _recall).
        return "Что ты помнишь о собеседнике (от первого лица записаны его собственные слова):\n" + numbered(general)

    def messages(self):
        # The fixed instructions and the tools come first and never change, so Ollama keeps them
        # cached; the mode and the facts follow as a message of their own, so a new fact or a switch
        # to "Личное" costs only that message, not the whole prompt.
        return [{"role": "system", "content": self.system},
                {"role": "system", "content": self.facts}] + self.history

    def reset(self):
        self.history = []
        self.tail = []
        self.facts = self._facts()
        self.since = time.time()

    def refresh_facts(self):
        """The facts the model reads, again: after the program itself changed them ("забудь, что я
        люблю кофе" - the model kept saying it for the rest of the conversation, from its snapshot)."""
        self.facts = self._facts()

    def unlock(self, private: Memory):
        """The phone's key opened the personal memory."""
        self.private = private
        self.locked = False
        self.facts = self._facts()

    def set_personal(self, on):
        """Switch the section; returns whether anything changed. A fresh conversation either way,
        so what was said in one section does not carry over into the other."""
        on = bool(on) and self.private is not None
        if on == self.personal:
            return False
        self.personal = on
        self.reset()
        return True

    def forget_said(self, stem):
        """The exchanges of this conversation that mention [stem], out of what the model reads. -> how many."""
        low = stem.lower().replace("ё", "е")
        pattern = re.compile(r"(?<!\w)" + re.escape(low))

        def said(m):
            return m.get("role") in ("user", "assistant") and pattern.search(str(m.get("content", "")).lower().replace("ё", "е"))
        before = len(self.history) + len(self.tail)
        self.history = [m for m in self.history if not said(m)]
        self.tail = [m for m in self.tail if not said(m)]
        return before - len(self.history) - len(self.tail)

    def warmup(self):
        return self.llm.warmup(self.messages(), TOOLS)

    def _recall(self, text):
        """Notes and past exchanges related to the phrase: in «Личное» both memories, marked by where they are
        from; in the ordinary section the general one only — nothing of «Личное» comes into it (the owner
        asked that it remember nothing of «Личное» there; up to two of its turns and notes were brought in)."""
        mine = self.active
        # past exchanges of this very conversation are in the history already: not again (each costs
        # its length in prompt reading, ~2 s per 100 tokens here)
        # past exchanges only when the phrase is about the past: "ты вообще умный?" brought three chats of
        # other days ("Вы цените оперативность…"), and the model, told they belong to the phrase, retold them
        past = PAST_TALK.search(text) is not None
        items = [describe(i) for i in mine.recall(text)
                 if i["kind"] != "turn" or past and i["at"] < int(self.since)]
        if self.personal:
            items += ["(общее) %s" % describe(i) for i in self.memory.recall(text, limit=2, facts=False)]
        if self.planner is not None:  # the notes live in the Planner
            notes = self.planner.notes_recall(text)
            items = (["(общее) " + n for n in notes] if self.personal else notes) + items
        return items[:4]

    def ask(self, text, now=None):
        """Yield the reply in pieces as it is generated; the first conversation of a morning ends with the day."""
        said = ""
        for piece in self._ask(text, now):
            said += piece
            yield piece
        note = self._morning_note(now or datetime.now()) if said.strip() else None
        if note:
            piece = " " + note
            self.last_reply = (self.last_reply + piece).strip()
            yield piece

    def _morning_note(self, now):
        """In the morning, the first answer of the day brings the day along: what is ahead, the weather, a warning
        ("Доброе утро" said by the owner already brings it: greet's own)."""
        if not self.briefing or self.personal or not 5 <= now.hour < 12 or self.handled in NO_BRIEFING:
            return None
        day = (now - timedelta(hours=4)).date().isoformat()  # a day starts at 4 in the morning, as greet's
        if self.memory.meta("summary_day") == day:
            return None
        self.memory.meta("summary_day", day)
        return "Кстати, доброе утро. " + self.skills._day_summary(now)

    def _ask(self, text, now=None):
        if self.clock() - self.last_turn > self.config.idle_reset:
            if not self.set_personal(False):
                self.reset()
        command = mode_command(text) if self.private is not None or self.locked else None
        # who answered and what was called: this phrase's, not the last one's (the server ends the conversation
        # after a "stop" by this, and a mode switch said right after one kept it)
        self.handled, self.calls = None, []
        if command is not None:
            self.handled = "mode"
        if command is not None and self.locked:
            self.last_turn = self.clock()
            yield "Личное закрыто: ключ хранится на телефоне, а телефон его ещё не передал."
            return
        if command is not None:
            self.last_turn = self.clock()
            changed = self.set_personal(command)
            if command:
                yield "Слушаю. Это останется между нами." if changed else "Мы и так в личном."
            else:
                yield "Вернулись к обычному." if changed else "Мы и не в личном."
            return
        # The scenarios the program answers by itself (skills.py, intents_ru.txt) come first:
        # milliseconds instead of seconds, and nothing made up.
        self._now = now
        routed = self.skills.handle(text, now)
        self.handled = self.skills.handled
        if self.skills.rewritten:  # "напомни, как зовут актёра…" is "как зовут актёра…" for the model too
            text = self.skills.rewritten
        self.calls = []
        # one request, or two in one phrase ("что у меня завтра и какая погода?"), each part said in turn
        parts = routed if isinstance(routed, list) else [routed]
        said, contexts, rest, spoken = [], [], False, ""
        for part in parts:
            while isinstance(part, Lookup):  # over the network: say something first, then go
                if part.prelude:
                    piece = (" " if spoken else "") + part.prelude
                    spoken += piece
                    yield piece
                part = part.fetch()
            if part is None:
                rest = True  # not the program's after all: the model answers, knowing what was said
            elif isinstance(part, Context):
                contexts.append(part)
            elif part:
                piece = (" " if spoken else "") + part
                spoken += piece
                said.append(part)
                yield piece
        if not rest and not contexts:
            self.last_turn = self.clock()
            reply = " ".join(said)
            if reply and self.handled == "forget" and reply == "Хорошо, забыл.":
                self.tail = []  # "забудь про Тимура" would keep the name it asks to forget
                self.last_reply = reply
            elif reply:  # "" is to stay silent ("ладно", "хватит")
                self.tail = [{"role": "user", "content": text.strip()}, {"role": "assistant", "content": reply}]
                self.active.add_turn(text, reply)
                self.last_reply = reply
                self._trim()
            return
        self.handled = "модель"
        self.calls = [c.tool for c in contexts if c.tool]
        # The model rarely thinks of searching by itself ("какой у меня код от домофона?" got
        # "не знаю" 3 times of 3), so what the memory finds for the phrase comes along with it.
        found = self._recall(text)
        extra = "\n[из памяти: %s]" % "; ".join(found) if found else ""
        for context in contexts:
            if not context.tool:
                extra += "\n[%s]" % context
        plans = self._plans_for(text, contexts, now)
        if plans:
            extra += "\n[из планировщика — для ответа, если вопрос касается этого; иначе не упоминай: %s]" % plans
        if WEATHER_TALK.search(text) and self.skills.weather is not None and not contexts:
            forecast = self.skills.weather_text(text if _has_day(text) else "", "", now=now)
            if not forecast.startswith("ошибка"):
                extra += "\n[прогноз погоды дома: %s]" % forecast
        if said:
            extra += "\n[уже сказано вслух: «%s» — не повторяй, ответь на остальное]" % " ".join(said)
        when = "\n[%s]" % stamp(now) if ABOUT_TIME.search(text) else ""
        self.history += self.tail
        self.tail = []
        begin = len(self.history)
        self.history.append({"role": "user", "content": "%s%s%s" % (text.strip(), when, extra)})
        for context in contexts:
            if context.tool:  # found for it by the program: in the history as its own tool's call and result,
                name, arguments = context.tool  # so it answers from it and does not search again (twice as slow)
                self.history.append({"role": "assistant", "content": "",
                                     "tool_calls": [{"function": {"name": name, "arguments": arguments}}]})
                self.history.append({"role": "tool", "tool_name": name, "content": str(context)})
        lead = " " if spoken else ""
        spoke = False
        acted = False  # a tool was called in this turn
        searched = any(c.tool or c.tried for c in contexts)  # the program has searched for it already
        # an action asked for, or a bit about what was just planned ("а её на час позже", "не, верни как было"):
        # the whole answer is read first - "Тренировку перенесу на 20:00" moved nothing
        hold_all = ACTION_REQUEST.search(text) is not None or (
            self.planner is not None and bool(self.planner.focus) and PLAN_BIT.search(text) is not None)
        # (not after every plan listing: "какой там ветер завтра" got the tail "записать это я не смог")
        risky = hold_all or PLANS_TALK.search(text) is not None or SELF_TALK.search(text) is not None
        answer = ""
        self._say_now = None
        nudges = []  # asked once more after an empty answer: out of the history afterwards
        try:
            retried = False  # a claim with no call is asked for once more before "не сделал"
            for _ in range(MAX_TOOL_ROUNDS + 1):
                content, calls, held = "", [], ""
                again = False
                gap = spoke  # this round's words follow what was already said: a space between
                start, checked = "", acted or spoke  # the first words, held until they are no claim
                gate = ""  # the words since the last sentence end, not said yet (see "risky")
                swallow = False  # the rest of a round whose start was a call written as text
                # an answer from search results: short (the model tended to retell all of them, 40 s),
                # and said by whole sentences, so that one cut by the limit is not heard half-way
                limit = SEARCH_REPLY_TOKENS if "найдено в интернете" in str(self.history[-1].get("content")) else None
                for chunk in self.llm.chat(self.messages(), TOOLS, num_predict=limit):
                    msg = chunk.get("message") or {}
                    piece = msg.get("content") or ""
                    if piece and limit:
                        held += piece
                        ends = list(SENTENCE_END.finditer(held))
                        cut = ends[-1].end() if ends else 0
                        piece, held = held[:cut], held[cut:]
                    if chunk.get("done") and held.strip() and (chunk.get("done_reason") != "length" or not (spoke or piece)):
                        piece += held + ("…" if chunk.get("done_reason") == "length" else "")
                        held = ""
                    calls += msg.get("tool_calls") or []
                    if swallow:
                        piece = ""
                    if not checked:
                        start += piece
                        piece = ""
                        if hold_all and not chunk.get("done"):
                            continue  # an action was asked for: the whole answer first
                        if re.match(r"\s*[a-z_<]", start) and "}" not in start and not chunk.get("done"):
                            continue  # latin letters first: maybe a call written as text, wait for its "}"
                        call = text_call(start)
                        if call:
                            calls.append(call)
                            checked, swallow, start = True, True, ""
                            continue
                        if hold_all and not calls and claims_done(start):
                            if not retried:
                                again = True  # said done, nothing called: once more
                                break
                            start = NOT_DONE
                        elif hold_all and calls:
                            start = ""  # what it said before its call: the call's result speaks
                        elif CLAIM.match(start):
                            if not chunk.get("done"):
                                continue  # a claim: wait for the end, a call may still come
                            offer = re.search(r"(?:запомню|запишу|добавлю|отмечу|удалю|перенесу|сохраню|зафиксирую)", start, re.I) \
                                and re.search(r"(?<!\w)(?:если|могу|хотите|можно)(?!\w)|\?", start, re.I)
                            # "Добавлю, если скажете время." is an offer, not a claim: said as it is
                            if not offer and not calls and not retried:
                                again = True  # the same once more: Gemma 4 calls the tool in 50-80% of tries
                                break
                            if not offer:
                                start = "" if calls else NOT_DONE
                        elif len(start.strip()) < 12 and not chunk.get("done"):
                            continue
                        elif re.match(r"\s*[(\[]", start) and not re.search(r"[)\]]\s*\S", start) and len(start) < 220 \
                                and not chunk.get("done"):
                            continue  # "(Сегодня 12:00) …": a stamp written back, cut off once it is closed and followed
                        if self.gemma and calls:
                            start = ""  # "Планы на вторник посмотреть." said before the call itself: not said
                        checked, piece = True, without_bracket_start(start)
                    # about the owner, the plans or the memory: each sentence is read before it is said, and one that says
                    # something was done with nothing called is not ("Понятно. Добавил в память, что ты играешь на гитаре",
                    # "Напомню тебе через 20 минут", "ЕГЭ перенесён на 20:00": none of it happened)
                    if risky and not acted and not swallow and (piece or (chunk.get("done") and gate)):
                        gate += piece
                        cut = len(gate) if chunk.get("done") else max((e.end() for e in SENTENCE_END.finditer(gate)), default=0)
                        piece, gate = gate[:cut], gate[cut:]
                        if piece.strip() and not calls and said_done(piece):
                            # the sentences before the claim are said; the claim and what follows it are not
                            bounds = [0] + [e.end() for e in SENTENCE_END.finditer(piece)] + [len(piece)]
                            keep = ""
                            for a_, b_ in zip(bounds, bounds[1:]):
                                if said_done(piece[a_:b_]):
                                    break
                                keep = piece[:b_]
                            before = spoke or content.strip() or keep.strip()
                            piece = keep + ((" " + NOT_DONE_AFTER) if before else NOT_DONE)
                            swallow, gate = True, ""
                    if piece:
                        if not spoke:
                            piece = lead + piece.lstrip()
                        elif gap:
                            piece = " " + piece.lstrip()
                        gap = False
                        content += piece
                        spoke = True
                        yield piece
                    if chunk.get("done"):
                        self.stats = chunk
                if again:
                    retried = True
                    continue
                # nothing said and nothing called: Gemma 4 wrote its call as "call{web_search{…}}", and
                # Ollama's parser dropped it ("gemma4 tool call parsing failed" in its log; 10 times)
                lost = not content and not calls
                answer += content
                reply = {"role": "assistant", "content": content.strip()}
                if calls:
                    reply["tool_calls"] = calls
                self.history.append(reply)
                searchable = not calls and not acted and not searched and self.search is not None and not self.personal
                promise = SEARCH_PROMISE.search(content)
                offer = promise and OFFER.search(content)
                if searchable and offer and content.rstrip().endswith("?"):
                    self.skills.offer_search(search_query(text))  # asked "поискать?": the next "да" searches
                elif searchable and (promise and not offer or lost and QUESTION.search(text) and not hold_all
                                     and not PLANS_TALK.search(text) and not WEATHER_TALK.search(text)):
                    # "Позвольте мне поискать." - and no search called: the program searches, and the model
                    # answers from what is found (the call alone in the history, as when it calls itself:
                    # after its own promise and the results it once said nothing more)
                    calls = [{"function": {"name": "web_search", "arguments": {"query": search_query(text)}}}]
                    reply["content"] = ""
                    reply["tool_calls"] = calls
                elif lost and not nudges:
                    self.history[-1] = {"role": "user", "content": NUDGE_AFTER if acted or searched else NUDGE}
                    nudges.append(self.history[-1])
                    continue
                if not calls:
                    break
                acted = True
                results = []
                for call in calls:
                    fn = call.get("function") or {}
                    self.calls.append((fn.get("name"), fn.get("arguments")))
                    result = self.run_tool(fn.get("name"), fn.get("arguments"))
                    results.append((fn.get("name"), result))
                    self.history.append({"role": "tool", "tool_name": fn.get("name", ""), "content": result})
                quick = None
                if not spoke and self._say_now:  # the program asks it itself: "Удалить …?"
                    quick = self._say_now
                elif not spoke and all(name in QUICK_REPLY and not result.startswith(("ошибка", "нет ", "уточни"))
                                       for name, result in results):
                    quick = QUICK_REPLY[results[-1][0]] or results[-1][1]
                elif not spoke and all(name in PLANNER_WRITES for name, _ in results):
                    # "не нашёл", "какое именно": said as the planner put it; the model once turned
                    # "нет такого" into "Удалено."
                    result = results[-1][1]
                    quick = "Не получилось: %s." % result[len("ошибка: "):].rstrip(".") if result.startswith("ошибка") \
                        else _say(result)
                if quick:
                    quick = lead + quick
                    self.history.append({"role": "assistant", "content": quick.strip()})
                    spoke = True
                    answer += quick
                    yield quick
                    break
            if not spoke:
                # something done and nothing said about it: "Готово."; nothing done: no word of "done"
                done = acted and any(name not in READ_ONLY for name, _ in self.calls)
                answer = lead + ("Готово." if done else NO_ANSWER)
                yield answer
            # kept to be found later: "что я говорил про Виктора"
            full = (spoken + answer).strip()
            self.active.add_turn(text, full)
            self.last_reply = full
        finally:
            self.last_turn = self.clock()
            if nudges:
                self.history = [m for m in self.history if not any(m is n for n in nudges)]
            self._compact(begin)
            self._trim()

    def _compact(self, begin):
        """After an answer from search results the results themselves go from the history: every later
        phrase would carry their ~400 tokens, and the history would be cut sooner (a whole re-read,
        20-30 s: seen after three searches in a row). The question and the answer stay."""
        def searched(m):
            return (m["role"] == "tool" and m.get("tool_name") == "web_search") or (
                m["role"] == "assistant" and m.get("tool_calls") and not m.get("content") and
                all((c.get("function") or {}).get("name") == "web_search" for c in m["tool_calls"]))
        turn = self.history[begin:]
        if any(searched(m) for m in turn):
            self.history[begin:] = [m for m in turn if not searched(m)]

    def _plans_for(self, text, contexts, now):
        """The planner's days a phrase about the plans is about, for the model to answer from: asked
        "какое число и что по плану?", it made up three meetings rather than look."""
        # a day named counts too: to "бла бла карандаш вторник" it said "на сегодня событий нет", having looked at nothing
        if self.planner is None or self.personal or not (PLANS_TALK.search(text) or _has_day(text)):
            return ""
        if any(str(c).startswith("из планировщика") or c.tool for c in contexts):
            return ""
        today = (now or datetime.now()).date()
        span = period(text, today) if _has_day(text) else None
        try:
            return self.planner.listing(*(span or (today, today + timedelta(days=1))))
        except (Unavailable, ValueError):
            return ""

    def run_tool(self, name, args):
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                return "ошибка: аргументы не в JSON"
        args = args or {}
        m = self.active
        try:
            if name == "remember":
                return "запомнено под номером %d" % m.remember(args["fact"])
            if name == "update_memory":
                return "обновлено" if m.update_fact(args["id"], args["fact"]) else "нет факта с таким номером"
            if name == "forget":
                return "забыто" if m.forget(args["id"]) else "нет факта с таким номером"
            planner_notes = self.planner is not None and not self.personal
            if name == "add_note" and planner_notes:
                self.planner.note_add(args.get("title") or "", args["text"])
                return "заметка сохранена в планировщике"
            if name == "find_notes" and planner_notes:
                lines = self.planner.tool_notes(args.get("query", ""))
                return "\n".join(lines) if lines else "ничего не найдено"
            if name == "delete_note" and planner_notes:
                note = self.planner.note_ids.get(int(args["id"]))
                if note is None:
                    return "нет заметки с таким номером: сначала find_notes"
                self.planner.note_delete(note)
                return "удалено"
            if name == "add_note":
                return "заметка сохранена под номером %d" % m.add_note(args["text"], args.get("title") or "")
            if name == "find_notes":
                query = args.get("query", "")
                items = m.recall(query, limit=5) if query.strip() else []
                if items:
                    return "\n".join(describe(i) for i in items)
                notes = m.find_notes(query)
                if not notes:
                    return "ничего не найдено"
                return "\n".join("[%d] %s%s (%s)" % (
                    n["id"], n["title"] + ": " if n["title"] else "", n["text"],
                    datetime.fromtimestamp(n["updated"]).strftime("%d.%m.%Y")) for n in notes)
            if name == "delete_note":
                return "удалено" if m.delete_note(args["id"]) else "нет заметки с таким номером"
            if name == "weather":
                return self.skills.weather_text(args.get("when") or "", args.get("city") or "", now=self._now)
            if name == "web_search":
                if self.search is None or self.personal:
                    return "ошибка: поиск в интернете здесь недоступен"
                try:
                    results = self.search.web_results(args["query"])
                except Offline:
                    return "ошибка: поиск сейчас не отвечает"
                return found_online(args["query"], results) if results else "ничего не найдено"
            if name in ("plans", "add_plan", "plan_done", "delete_plan", "restore_plan"):
                if self.planner is None:
                    return "ошибка: планировщик не подключён"
                if name == "plans":
                    return self.planner.plans(args.get("when") or "")
                if name == "add_plan":
                    return self.planner.add(args["text"])
                if name == "plan_done":
                    return self.planner.done(args["query"], args.get("when") or "")
                if name == "restore_plan":
                    return self.planner.restore()
                # the program asks "Удалить …?" itself and takes the yes or no of the next phrase (skills.py):
                # the model once took "нет, не удаляй" for a yes, and once confirmed its own question
                item, problem = self.planner._find(args.get("query") or "", args.get("when") or "")
                if problem:
                    return problem
                self._say_now = self.skills.confirm_delete(item)
                return "спросил, удалить ли: %s" % self.planner.describe(item)
            return "ошибка: нет инструмента %s" % name
        except (KeyError, ValueError, TypeError, Unavailable) as exc:
            return "ошибка: %s" % exc

    def _size(self, messages):
        return sum(len(m.get("content") or "") + 20 for m in messages) // CHARS_PER_TOKEN

    def _trim(self):
        """History over ~75% of the room left after the system prompt and tools:
        keep only the last ~30% of it, starting at a user message. Rare big cuts keep the cache useful."""
        room = self.config.num_ctx * 0.75 - self._size([{"content": self.system}, {"content": self.facts}]) - TOOLS_TOKENS
        if self._size(self.history) < room:
            return
        keep, size = len(self.history), 0
        for i in range(len(self.history) - 1, -1, -1):
            size += self._size([self.history[i]])
            if size > room * 0.3 and keep < len(self.history):  # the last exchange stays, however long
                break
            if self.history[i]["role"] == "user":
                keep = i
        self.history = self.history[keep:]
        self.facts = self._facts()
