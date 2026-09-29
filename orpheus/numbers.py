"""Numbers for the voice, and numbers from it.

speakable(): the digits of a reply written out as words that agree with the words around them,
right before synthesis. Piper reads text through espeak-ng, which says digits as bare numbers in
the nominative: "с 12:00 до 13:30" would not become "с двенадцати часов до тринадцати тридцати",
nor "26 сентября" "двадцать шестое сентября", nor "1 минута" "одна минута". The phone still shows
the digits: only what is synthesized changes.

to_digits(): number words in what was heard, as digits ("через два часа" -> "через 2 часа").
The recogniser writes most numbers as digits itself ("в 8 вечера", "15:30", "20% от 3 000"),
but not all of them.
"""

import re

# ---------------------------------------------------------------------------------------------
# words for numbers

UNITS = {
    "nom": ["", "один", "два", "три", "четыре", "пять", "шесть", "семь", "восемь", "девять"],
    "gen": ["", "одного", "двух", "трёх", "четырёх", "пяти", "шести", "семи", "восьми", "девяти"],
    "dat": ["", "одному", "двум", "трём", "четырём", "пяти", "шести", "семи", "восьми", "девяти"],
}
UNITS_F = {"nom": {1: "одна", 2: "две"}, "gen": {1: "одной"}, "dat": {1: "одной"}}
UNITS_N = {"nom": {1: "одно"}}
TEENS = {
    "nom": ["десять", "одиннадцать", "двенадцать", "тринадцать", "четырнадцать", "пятнадцать", "шестнадцать",
            "семнадцать", "восемнадцать", "девятнадцать"],
    "gen": ["десяти", "одиннадцати", "двенадцати", "тринадцати", "четырнадцати", "пятнадцати", "шестнадцати",
            "семнадцати", "восемнадцати", "девятнадцати"],
}
TEENS["dat"] = TEENS["gen"]
TENS = {
    "nom": ["", "", "двадцать", "тридцать", "сорок", "пятьдесят", "шестьдесят", "семьдесят", "восемьдесят", "девяносто"],
    "gen": ["", "", "двадцати", "тридцати", "сорока", "пятидесяти", "шестидесяти", "семидесяти", "восьмидесяти", "девяноста"],
}
TENS["dat"] = TENS["gen"]
HUNDREDS = {
    "nom": ["", "сто", "двести", "триста", "четыреста", "пятьсот", "шестьсот", "семьсот", "восемьсот", "девятьсот"],
    "gen": ["", "ста", "двухсот", "трёхсот", "четырёхсот", "пятисот", "шестисот", "семисот", "восьмисот", "девятисот"],
    "dat": ["", "ста", "двумстам", "трёмстам", "четырёмстам", "пятистам", "шестистам", "семистам", "восьмистам", "девятистам"],
}
ZERO = {"nom": "ноль", "gen": "ноля", "dat": "нолю"}

# a noun after a number, by case: (after 1, after 2-4, after 5-20)
NOUNS = {
    "тысяча": {"nom": ("тысяча", "тысячи", "тысяч"), "gen": ("тысячи", "тысяч", "тысяч"), "dat": ("тысяче", "тысячам", "тысячам")},
    "миллион": {"nom": ("миллион", "миллиона", "миллионов"), "gen": ("миллиона", "миллионов", "миллионов"),
                "dat": ("миллиону", "миллионам", "миллионам")},
    "час": {"nom": ("час", "часа", "часов"), "gen": ("часа", "часов", "часов"), "dat": ("часу", "часам", "часам")},
    "минута": {"nom": ("минута", "минуты", "минут"), "gen": ("минуты", "минут", "минут"), "dat": ("минуте", "минутам", "минутам")},
    "день": {"nom": ("день", "дня", "дней"), "gen": ("дня", "дней", "дней"), "dat": ("дню", "дням", "дням")},
    "процент": {"nom": ("процент", "процента", "процентов"), "gen": ("процента", "процентов", "процентов"),
                "dat": ("проценту", "процентам", "процентам")},
}


def plural(n, one, few, many):
    """The form of a noun after the number n: 1 -> one, 2-4 -> few, 5-20 -> many (21 -> one...)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def noun(n, word, case="nom"):
    forms = NOUNS[word][case]
    if case == "nom":
        return plural(n, *forms)
    return forms[0] if n % 10 == 1 and n % 100 != 11 else forms[1]


def _below_1000(n, case, gender):
    words = []
    h, rest = divmod(n, 100)
    if h:
        words.append(HUNDREDS[case][h])
    if 10 <= rest < 20:
        words.append(TEENS[case][rest - 10])
    else:
        t, u = divmod(rest, 10)
        if t:
            words.append(TENS[case][t])
        if u:
            special = (UNITS_F if gender == "f" else UNITS_N if gender == "n" else {}).get(case, {})
            words.append(special.get(u) or UNITS[case][u])
    return words


def cardinal(n, case="nom", gender="m"):
    """17 -> "семнадцать"; case "gen"/"dat" ("с семнадцати", "к семнадцати"); gender "f"/"n" for 1 and 2."""
    n = int(n)
    if n < 0:
        return "минус " + cardinal(-n, case, gender)
    if n == 0:
        return ZERO[case]
    if n >= 10 ** 9:
        return str(n)
    words = []
    millions, rest = divmod(n, 10 ** 6)
    thousands, rest = divmod(rest, 1000)
    if millions:
        words += (_below_1000(millions, case, "m") if millions > 1 else []) + [noun(millions, "миллион", case)]
    if thousands:
        words += (_below_1000(thousands, case, "f") if thousands > 1 else []) + [noun(thousands, "тысяча", case)]
    if rest:
        words += _below_1000(rest, case, gender)
    return " ".join(words)


ORD_STEMS = {
    1: "перв", 2: "втор", 3: "трет", 4: "четвёрт", 5: "пят", 6: "шест", 7: "седьм", 8: "восьм", 9: "девят",
    10: "десят", 11: "одиннадцат", 12: "двенадцат", 13: "тринадцат", 14: "четырнадцат", 15: "пятнадцат",
    16: "шестнадцат", 17: "семнадцат", 18: "восемнадцат", 19: "девятнадцат", 20: "двадцат", 30: "тридцат",
    40: "сороков", 50: "пятидесят", 60: "шестидесят", 70: "семидесят", 80: "восьмидесят", 90: "девяност",
    100: "сот", 200: "двухсот", 300: "трёхсот", 400: "четырёхсот", 500: "пятисот", 600: "шестисот",
    700: "семисот", 800: "восьмисот", 900: "девятисот", 1000: "тысячн", 2000: "двухтысячн", 3000: "трёхтысячн",
}
ORD_ENDINGS = {
    "hard": {"m": "ый", "n": "ое", "f": "ая", "gen": "ого", "dat": "ому", "prep": "ом"},
    "stressed": {"m": "ой", "n": "ое", "f": "ая", "gen": "ого", "dat": "ому", "prep": "ом"},
    "soft": {"m": "ий", "n": "ье", "f": "ья", "gen": "ьего", "dat": "ьему", "prep": "ьем"},
}


def _ord_word(n, form):
    kind = "soft" if n == 3 else "stressed" if n in (2, 6, 7, 8, 40) else "hard"
    return ORD_STEMS[n] + ORD_ENDINGS[kind][form]


def ordinal(n, form="m"):
    """26 -> "двадцать шестой"; form: m, n, f (nominative), gen, dat, prep.
    Only the last word of an ordinal changes: "две тысячи двадцать шестого"."""
    n = int(n)
    if n in ORD_STEMS:
        return _ord_word(n, form)
    for base in (1000, 100, 10):
        tail = n % base
        if n > base and tail:
            return cardinal(n - tail) + " " + ordinal(tail, form)
    if n % 100 == 0 and n < 1000:
        return _ord_word(n, form)
    return cardinal(n)


# ---------------------------------------------------------------------------------------------
# a reply made speakable

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
GEN_PREP = {"с", "со", "до", "от", "после", "около", "возле", "без", "для", "из", "кроме", "вместо", "у",
            "более", "менее", "больше", "меньше", "свыше", "началу", "середины", "конца", "раньше", "позже"}
DAT_PREP = {"к", "ко"}
# nouns that make 1 and 2 feminine ("одна минута", "две задачи") or neuter ("одно событие")
FEMININE = re.compile(r"^(минут|секунд|недел|задач|заметк|заметок|штук|встреч|пар[аыу]?$|копе|лекци|книг|"
                      r"ноч|сотн|тысяч|строк|попытк|попыток|ошибк|ошибок|чашк|чашек|таблетк|таблеток|"
                      r"[иий]ен[аыу]?$|гривн|рупи|лир[аыу]?$)", re.I)
NEUTER = re.compile(r"^(событи|дел[оа]?$|занят|сообщени|письм|окн|яйц|очк)", re.I)
# a code or a number said digit by digit: "код от домофона 4521" -> "четыре пять два один"
DIGITWISE = re.compile(r"(код|пароль|пин|номер|телефон)\w*\W+(?:\w+\W+){0,3}$", re.I)


def _preposition_form(text):
    """"с второго" -> "со второго", "к второму" -> "ко второму", "с ста" -> "со ста"."""
    return re.sub(r"(?<!\w)([сСкК])\s+(?=втор|ст[оа](?!\w))", r"\1о ", text)


def _case_before(text, start):
    """The case a number takes from the word right before it."""
    m = re.search(r"(\w+)[\s ]*$", text[:start])
    word = m.group(1).lower() if m else ""
    if word in GEN_PREP:
        return "gen"
    if word in DAT_PREP:
        return "dat"
    return "nom"


def _say_time(h, m, case):
    if h == 0 and m == 0:
        return {"nom": "полночь", "gen": "полуночи", "dat": "полуночи"}[case]
    hours = cardinal(h, case)
    if m == 0:
        return "%s %s" % (hours, noun(h, "час", case))
    minutes = cardinal(m, case, "f") if m >= 10 else "ноль " + cardinal(m, case, "f")
    return "%s %s" % (hours, minutes)


def _year(n, form):
    return ordinal(n, form)


def speakable(text):
    """Digits, times, dates and percents as words for the voice (see the module doc)."""
    if not re.search(r"\d", text):
        return text

    def date_words(m, day, month, year):
        form = {"gen": "gen", "dat": "dat"}.get(_case_before(text, m.start()), "n")
        out = "%s %s" % (ordinal(day, form), MONTHS_GEN[month - 1])
        if year:
            out += " %s года" % _year(year, "gen")
        return out

    # 26.09 and 26.09.2026
    def numeric_date(m):
        day, month = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        if not (1 <= day <= 31 and 1 <= month <= 12):
            return m.group(0)
        return date_words(m, day, month, year)

    text = re.sub(r"(?<![\d.,:])(\d{1,2})\.(\d{1,2})(?:\.(\d{4}|\d{2}))?(?![\d.,:]?\d)", numeric_date, text)

    # 26 сентября [2026 [года|г.]]
    def word_date(m):
        day = int(m.group(1))
        if not 1 <= day <= 31:
            return m.group(0)
        month = MONTHS_GEN.index(m.group(2).lower()) + 1
        year = int(m.group(3)) if m.group(3) else None
        return date_words(m, day, month, year)

    text = re.sub(r"(?<![\d.,])(\d{1,2})\s+(" + "|".join(MONTHS_GEN) + r")(?:\s+(\d{4})(?:\s*(?:года|г\.?)(?!\w))?)?(?!\w)",
                  word_date, text, flags=re.I)

    # 2026 год / года / году
    def year_noun(m):
        form = {"год": "m", "года": "gen", "году": "prep", "годом": "m"}[m.group(2).lower()]
        return "%s %s" % (_year(int(m.group(1)), form), m.group(2))

    text = re.sub(r"(?<![\d.,])(\d{4})\s+(год|года|году|годом)(?!\w)", year_noun, text, flags=re.I)

    # 17:24, 10:00 - with the case of the preposition before: "с 12:00" -> "с двенадцати часов"
    def time(m):
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            return m.group(0)
        return _say_time(h, mi, _case_before(text, m.start()))

    text = re.sub(r"(?<![\d.,:])([01]?\d|2[0-4]):([0-5]\d)(?![\d:])", time, text)

    # "+13", "-5" (a sign stuck to the number, not "10 - 11"); "2 м/с"
    text = re.sub(r"(?<![\w.,:])\+(?=\d)", "плюс ", text)
    text = re.sub(r"(?<![\w.,:])[-−](?=\d)", "минус ", text)
    text = re.sub(r"(?<![\d.,])(\d+)\s*м/с(?!\w)",
                  lambda m: "%s %s в секунду" % (m.group(1), plural(int(m.group(1)), "метр", "метра", "метров")), text)

    # 15%, 2,5%
    def percent(m):
        whole = m.group(1).replace(" ", "").replace(" ", "")
        if m.group(2):
            return "%s %s" % (_decimal(whole, m.group(2)), "процента")
        n = int(whole)
        return "%s %s" % (cardinal(n, _case_before(text, m.start())), noun(n, "процент", _case_before(text, m.start())))

    text = re.sub(r"(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d+))?\s*%", percent, text)

    # 3,5 and 0.25
    text = re.sub(r"(?<![\d.,:])(\d+)[.,](\d+)(?![\d.,:]?\d)", lambda m: _decimal(m.group(1), m.group(2)), text)

    # "1-го", "2-й", "3-е"
    def ordinal_suffix(m):
        form = {"й": "m", "ый": "m", "ой": "m", "го": "gen", "ого": "gen", "е": "n", "ое": "n", "му": "dat",
                "ому": "dat", "я": "f", "ая": "f", "м": "prep", "ом": "prep"}.get(m.group(2).lower())
        return ordinal(int(m.group(1)), form) if form else m.group(0)

    text = re.sub(r"(?<!\d)(\d+)-(й|ый|ой|го|ого|е|ое|му|ому|я|ая|м|ом)(?!\w)", ordinal_suffix, text, flags=re.I)

    # whatever numbers are left: "3 000" is three thousand, agreeing with the next word
    def number(m):
        digits = m.group(1).replace(" ", "").replace(" ", "")
        if len(digits) >= 3 and DIGITWISE.search(text[max(0, m.start() - 40):m.start()]):
            return " ".join(ZERO["nom"] if d == "0" else UNITS["nom"][int(d)] for d in digits)
        n = int(digits)
        nxt = re.match(r"\w+", text[m.end():].lstrip())
        word = nxt.group(0) if nxt else ""
        gender = "f" if FEMININE.match(word) else "n" if NEUTER.match(word) else "m"
        return cardinal(n, _case_before(text, m.start()), gender)

    text = re.sub(r"(?<![\d.,:])(\d{1,3}(?:[  ]\d{3})+|\d+)(?![\d.,:]?\d)", number, text)
    return _preposition_form(text)


def _decimal(whole, frac):
    frac = frac[:2]
    w = int(whole)
    f = int(frac)
    if f == 0:
        return cardinal(w)
    denominator = ("десятая", "десятых") if len(frac) == 1 else ("сотая", "сотых")
    return "%s %s %s %s" % (cardinal(w, gender="f"), "целая" if w % 10 == 1 and w % 100 != 11 else "целых",
                            cardinal(f, gender="f"), denominator[0] if f % 10 == 1 and f % 100 != 11 else denominator[1])


# ---------------------------------------------------------------------------------------------
# number words -> digits

def _forms():
    """Every case form of the number words -> (value, kind); kind: unit/teen/ten/hundred/thousand/ordinal."""
    words = {}

    def add(word, value, kind):
        words.setdefault(word.replace("ё", "е"), (value, kind))

    for u, forms in enumerate(zip(*(UNITS[c] for c in UNITS))):
        for w in forms:
            if w:
                add(w, u, "unit")
    extra_units = {1: "одна одну одной одним одними одно одном", 2: "две двумя", 3: "тремя", 4: "четырьмя",
                   5: "пятью", 6: "шестью", 7: "семью", 8: "восемью", 9: "девятью"}
    for u, ws in extra_units.items():
        for w in ws.split():
            add(w, u, "unit")
    for i, forms in enumerate(zip(*(TEENS[c] for c in TEENS))):
        for w in forms:
            add(w, 10 + i, "teen")
            add(w[:-1] + "ью" if w.endswith("ь") else w, 10 + i, "teen")
    for t, forms in enumerate(zip(*(TENS[c] for c in TENS))):
        for w in forms:
            if w:
                add(w, t * 10, "ten")
    add("двадцатью", 20, "ten")
    add("тридцатью", 30, "ten")
    for h, forms in enumerate(zip(*(HUNDREDS[c] for c in HUNDREDS))):
        for w in forms:
            if w:
                add(w, h * 100, "hundred")
    for w in "тысяча тысячи тысяч тысячу тысяче тысячей тысячам".split():
        add(w, 1000, "thousand")
    add("ноль", 0, "unit")
    add("ноля", 0, "unit")
    add("нуль", 0, "unit")
    for n, stem in ORD_STEMS.items():
        if n >= 1000:
            continue
        kind = "soft" if n == 3 else "stressed" if n in (2, 6, 7, 8, 40) else "hard"
        endings = set(ORD_ENDINGS[kind].values()) | ({"ую", "ой", "ых", "ым"} if kind != "soft" else {"ью", "ьей", "ьих", "ьим"})
        for e in endings:
            add(stem + e, n, "ordinal")
    return words


NUMBER_WORDS = _forms()
_ORDER = {"hundred": 3, "ten": 2, "teen": 1, "unit": 1}


def to_digits(text):
    """"через два часа" -> "через 2 часа", "двадцать пятого сентября" -> "25 сентября";
    other words are left as they are."""
    tokens = re.split(r"(\s+)", text)
    out = []
    i = 0
    while i < len(tokens):
        word = tokens[i]
        key = re.sub(r"[^\w]", "", word.lower().replace("ё", "е"))
        if key not in NUMBER_WORDS:
            out.append(word)
            i += 1
            continue
        total, group, last, j, used = 0, 0, 9, i, []
        trailing = ""
        while j < len(tokens):
            key = re.sub(r"[^\w]", "", tokens[j].lower().replace("ё", "е"))
            if key not in NUMBER_WORDS:
                break
            value, kind = NUMBER_WORDS[key]
            if kind == "thousand":
                total += (group or 1) * 1000
                group, last = 0, 9
            elif kind == "ordinal":
                if value < 1000 and _ORDER["unit" if value < 10 else "ten" if value < 100 else "hundred"] < last:
                    group += value
                    used.append(j)
                    trailing = re.sub(r"^\W*\w+", "", tokens[j])
                    j += 2
                break
            else:
                order = _ORDER[kind]
                if order >= last:
                    break
                group += value
                last = 0 if kind == "teen" else order
            used.append(j)
            trailing = re.sub(r"^\W*\w+", "", tokens[j])
            j += 2
        if not used:
            out.append(word)
            i += 1
            continue
        out.append(re.match(r"^\W*", tokens[i]).group(0) + str(total + group) + trailing)
        i = used[-1] + 1
    return "".join(out)
