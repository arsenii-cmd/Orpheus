"""Phrases the program answers by itself, described as sentence templates (intents_ru.txt).

This is how local voice assistants are built: Home Assistant's Assist with "prefer handling
commands locally" and Snips NLU (a deterministic parser before the statistical one) first match a
phrase against templates of what people say for each scenario - exact, instant, never inventing
anything - and only what no template covers goes to the language model. Here the model takes 3-8 s
and a template a fraction of a millisecond.

The templates use the syntax of Home Assistant's hassil:

    (а|б)        one of these: "(который|сколько) час"
    [а|б]        may be left out: "сколько [сейчас] времени"
    {слот}       any words, handed to the scenario: "сделай заметку {text}"
    {слот:а|б}   one of these words, handed to the scenario: "какой сейчас {unit:год|месяц}"
    <правило>    a piece defined once as "<правило> = ..." and used in many templates
    заметк(у|и)  alternatives inside a word

A phrase must match a template as a whole, whatever its case, "ё", punctuation and the polite or
filler words around it ("Орфей, скажи пожалуйста, который час?" is "который час"). When several
templates match, the one with more words of its own wins (the most specific), then the one
written first; a scenario may still decline ("сколько будет стоить ремонт" is no sum), and then
the next one is tried.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATES = Path(__file__).with_name("intents_ru.txt")

# Said around a request but not part of it. At the start: "Орфей, слушай, а скажи мне ..."
FILLERS_START = re.compile(
    # "эээ, добавь…", "тогда добавь…", "ой, нет, верни её" (the whole phrase became the title)
    r"^(?:\s*(?:орфей|слушай|послушай|эй|ну|а|ага|угу|и|так|скажи|скажите|подскажи|подскажите|окей|ок|алло|ой|упс|блин|хм|э+м*|м{2,}|"
    r"тогда|нет|короче|"
    r"будь\s+(?:добр|добра|любезен|любезна)|пожалуйста|привет|кстати|короче|значит)(?!\w)(?:\s+(?:мне|ка)(?!\w))*)+")
FILLERS_ANY = re.compile(r"(?<!\w)(?:пожалуйста|будь\s+(?:добр|добра|любезен|любезна))(?!\w)")
FILLERS_END = re.compile(r"(?:\s+(?:пожалуйста|орфей|плиз|спасибо))+\s*$")


def normalize(text):
    """Lower case, "ё" as "е", everything but letters and digits as spaces - of the same length,
    so that a slot found here is cut out of the original text with its punctuation and capitals."""
    out = []
    for c in text:
        low = c.lower()
        if len(low) != 1:
            low = c
        if low == "ё":
            low = "е"
        out.append(low if low.isalnum() else " ")
    return "".join(out)


def _blank(text, pattern):
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


# ---------------------------------------------------------------------------------------------
# templates -> regular expressions

_TOKEN = re.compile(r"\s+|[()\[\]|]|\{[^}]*\}|<[^>]+>|[^\s()\[\]|{}<>]+")


def _parse(tokens, i=0, closer=None):
    """-> (options, next index): options are sequences, a sequence is chunks (what stands between
    spaces), a chunk is parts: ("word", text), ("group", options, optional), ("slot", name, values),
    ("rule", name)."""
    options, chunk = [[]], []

    def end_chunk():
        nonlocal chunk
        if chunk:
            options[-1].append(chunk)
            chunk = []

    while i < len(tokens):
        t = tokens[i]
        if t.isspace():
            end_chunk()
        elif t == "|":
            end_chunk()
            options.append([])
        elif t in ")]":
            if t != closer:
                raise ValueError("лишняя скобка %r" % t)
            end_chunk()
            return options, i + 1
        elif t in "([":
            inner, i = _parse(tokens, i + 1, ")" if t == "(" else "]")
            chunk.append(("group", inner, t == "["))
            continue
        elif t.startswith("{"):
            name, _, values = t[1:-1].partition(":")
            chunk.append(("slot", name.strip(), values.strip()))
        elif t.startswith("<"):
            chunk.append(("rule", t[1:-1].strip()))
        else:
            chunk.append(("word", t))
        i += 1
    if closer:
        raise ValueError("не закрыта скобка %r" % closer)
    end_chunk()
    return options, i


def parse(text):
    return _parse(_TOKEN.findall(text))[0]


def _words(text):
    return r"\s+".join(re.escape(w) for w in normalize(text).split())


class _Compiler:
    """Every chunk of a sequence compiles with the spaces in front of it ("lead"), so a part left
    out leaves no double space behind; alternatives glued to a word ("заметк(у|и)") compile
    without them."""

    def __init__(self, rules):
        self.rules = rules
        self.names = {}

    def seq(self, seq, lead):
        return "".join(self.chunk(c, lead or k > 0) for k, c in enumerate(seq))

    def chunk(self, chunk, lead):
        space = r"\s+" if lead else ""
        if len(chunk) == 1 and chunk[0][0] in ("group", "rule"):
            return self.group(chunk[0], lead)
        if len(chunk) == 1 and chunk[0][0] == "slot":
            return space + self.slot(chunk[0])
        out = space
        for part in chunk:
            if part[0] == "word":
                out += _words(part[1])
            elif part[0] == "slot":
                out += self.slot(part)
            else:
                out += self.group(part, False)
        return out

    def group(self, part, lead):
        if part[0] == "rule":
            if part[1] not in self.rules:
                raise ValueError("нет правила <%s>" % part[1])
            options, optional = self.rules[part[1]], False
        else:
            options, optional = part[1], part[2]
        alts = [self.seq(o, lead) for o in options]
        if "" in alts:
            optional = True
            alts = [a for a in alts if a]
        body = "(?:%s)" % "|".join(alts)
        return body + "?" if optional else body

    def slot(self, part):
        name = part[1]
        n = self.names.get(name, 0)
        self.names[name] = n + 1
        group = name if n == 0 else "%s__%d" % (name, n)  # one name may stand in several alternatives
        if part[2]:
            return "(?P<%s>%s)" % (group, "|".join(_words(v) for v in part[2].split("|")))
        return "(?P<%s>.+?)" % group


@dataclass
class Template:
    intent: str
    text: str
    regex: re.Pattern
    order: int


@dataclass
class Match:
    intent: str
    slots: dict = field(default_factory=dict)
    score: int = 0
    template: str = ""


class Intents:
    def __init__(self, source):
        self.rules = {}
        self.templates = []
        intent = None
        lines = []
        for raw in source.splitlines():
            line = re.sub(r"\s+#.*$", "", raw).strip()
            if not line or line.startswith("#"):
                continue
            m = re.fullmatch(r"<([^>]+)>\s*=\s*(.+)", line)
            if m:
                self.rules[m.group(1).strip()] = parse(m.group(2))
                continue
            m = re.fullmatch(r"\[([a-z_][a-z0-9_]*)\]", line)
            if m:
                intent = m.group(1)
                continue
            if intent is None:
                raise ValueError("шаблон вне раздела: %r" % line)
            lines.append((intent, line))
        for order, (intent, line) in enumerate(lines):
            compiler = _Compiler(self.rules)
            try:
                body = "|".join(compiler.seq(option, True) for option in parse(line))
            except (ValueError, KeyError) as exc:
                raise ValueError("%s: %r: %s" % (intent, line, exc)) from None
            self.templates.append(Template(intent, line, re.compile(r"(?:%s)\s*" % body), order))

    @classmethod
    def load(cls, path=TEMPLATES):
        return cls(Path(path).read_text(encoding="utf-8"))

    @property
    def names(self):
        return list(dict.fromkeys(t.intent for t in self.templates))

    def match(self, text):
        """Every scenario whose template the phrase matches, the most specific first,
        one match per scenario."""
        norm = normalize(text)
        variants = [norm]
        bare = _blank(_blank(norm, FILLERS_ANY), FILLERS_END)
        start = FILLERS_START.match(bare)
        if start and re.search(r"\w", bare[start.end():]):
            bare = " " * start.end() + bare[start.end():]
        if bare != norm and re.search(r"\w", bare):
            variants.insert(0, bare)
        best = {}
        for variant in variants:
            padded = " " + variant
            for t in self.templates:
                m = t.regex.fullmatch(padded)
                if not m:
                    continue
                slots, slot_chars = {}, 0
                for name, value in m.groupdict().items():
                    if value is None:
                        continue
                    a, b = m.span(name)
                    slot_chars += len(re.sub(r"\W", "", padded[a:b]))
                    slots.setdefault(name.split("__")[0], _clean(text[a - 1:b - 1]))
                score = len(re.sub(r"\W", "", padded)) - slot_chars
                cur = best.get(t.intent)
                if cur is None or (score, -t.order) > (cur[0].score, -cur[1]):
                    best[t.intent] = (Match(t.intent, slots, score, t.text), t.order)
        return [m for m, _ in sorted(best.values(), key=lambda x: (-x[0].score, x[1]))]


def _clean(value):
    """A slot as said, without the punctuation, polite words and spaces around it."""
    value = FILLERS_ANY.sub(" ", value)
    value = re.sub(r"(?i)(?<!\w)пожалуйста(?!\w)", " ", value)
    return " ".join(value.split()).strip(" ,.;:!?—–-«»\"'")
