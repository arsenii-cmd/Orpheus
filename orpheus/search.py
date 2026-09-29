"""Search: Wikipedia for "кто такой …" / "что такое …", DuckDuckGo for the rest (through web.py);
the Central Bank's exchange rates ("какой курс доллара?": the search's snippets name the sites, not
the numbers).

Wikipedia's summary is said by the program itself - it is shorter and surer than what a 4B model
recalls; DuckDuckGo's results (titles, snippets, sites) are handed to the model to answer from.
Nothing found is opened: only the search pages themselves are read.
"""

import html
import re
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, urlsplit

from .web import Offline

WIKI = "https://ru.wikipedia.org/w/api.php"
DDG = "https://html.duckduckgo.com/html/"
CBR = "https://www.cbr.ru/scripts/XML_daily.asp"
RATES_KEPT = 3600  # the rates are set once a working day


@dataclass
class Result:
    title: str
    snippet: str
    site: str


@dataclass
class Rate:
    code: str
    nominal: int  # the rate is for this many units: 100 иен, 10 гривен
    value: float  # rubles

    @property
    def unit(self):
        return self.value / self.nominal


def _stems(text):
    words = re.findall(r"[a-zа-яё0-9]{3,}", text.lower().replace("ё", "е"))
    return [w[:max(3, len(w) - 2)] for w in words]


def clean_summary(text, sentences=2):
    """Wikipedia's first lines, speakable: no stress marks, no brackets with spellings and dates."""
    text = text.replace("́", "").replace(" ", " ")
    while True:  # innermost brackets first: "(… (…) …)"
        shorter = re.sub(r"\s*\([^()]*\)", "", text)
        if shorter == text:
            break
        text = shorter
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    # "Эмпа́тия или эмпати́я": two stresses, one word once they are gone
    text = re.sub(r"(?<!\w)(\w+)\s+или\s+\1(?!\w)", r"\1", text, flags=re.I)
    text = re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text)
    return " ".join(parts[:sentences]).strip()


class Search:
    def __init__(self, web, clock=time.monotonic):
        self.web = web
        self.clock = clock
        self._rates = {}  # the day asked for (None: the current) -> (when fetched, (rates, their date))

    def wikipedia(self, subject, person=False):
        """The start of the article about [subject], or None when there is none that surely is.
        person: "кто такой Пушкин" is not about the town of Пушкин."""
        data = self.web.get_json(WIKI, {
            "action": "query", "format": "json", "generator": "search", "gsrsearch": subject,
            "prop": "extracts", "exintro": 1, "explaintext": 1, "exsentences": 3, "redirects": 1, "exlimit": 10,
            "gsrlimit": 6 if person else 3,
        })
        pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 99))
        if person:  # articles about people are named "Пушкин, Александр Сергеевич": those first
            pages.sort(key=lambda p: ", " not in p.get("title", ""))
        wanted = set(_stems(subject))
        for page in pages[:6 if person else 3]:
            title = re.sub(r"\(.*?\)", "", page.get("title", ""))
            # "Пушкин, Александр Сергеевич": the surname is what was asked about, the name may be left out
            title_stems = _stems(title.split(",")[0]) if person else _stems(title)
            extract = page.get("extract") or ""
            if not title_stems or not extract or re.search(r"может означать|может относиться", extract[:200]):
                continue
            if person and re.search(r"—\s*(?:\w+\s+){0,2}(?:город|посёлок|поселок|село|деревня|река|район|станция|улица|"
                                    r"остров|озеро|гора|муниципальн)", clean_summary(extract, 1)[:160]):
                continue
            match = lambda s: any(w.startswith(s[:4]) or s.startswith(w[:4]) for w in wanted)  # noqa: E731
            hits = sum(1 for s in title_stems if match(s))
            # the article must be about the thing itself: "Потомки Пушкина" is not "кто такой Пушкин"
            if hits * 2 >= len(title_stems) and hits and match(title_stems[0]):
                summary = clean_summary(extract)
                if len(summary) > 20:
                    return summary
        return None

    def rates(self, day=None):
        """The Central Bank's official rates ({"USD": Rate, ...}, the date they are set for): the current
        ones, or those for [day] ("на завтра": the latest if it has not set them yet)."""
        kept = self._rates.get(day)
        if kept and self.clock() - kept[0] < RATES_KEPT:
            return kept[1]
        page = self.web.get(CBR, {"date_req": day.strftime("%d/%m/%Y")} if day else None)
        when = re.search(r'<ValCurs[^>]*Date="(\d\d)\.(\d\d)\.(\d{4})"', page)
        rates = {m.group(1): Rate(m.group(1), int(m.group(2)), float(m.group(3).replace(",", ".")))
                 for m in re.finditer(r"<CharCode>([A-Z]{3})</CharCode>\s*<Nominal>(\d+)</Nominal>.*?<Value>([\d.,]+)</Value>",
                                      page, re.S)}
        if not when or not rates:
            raise Offline("ответ ЦБ не разобрать")
        found = rates, date(int(when.group(3)), int(when.group(2)), int(when.group(1)))
        self._rates[day] = (self.clock(), found)
        return found

    def web_results(self, query, limit=5):
        if getattr(self.web, "searxng", ""):
            try:
                found = self._searxng(query, limit)
                if found:
                    return found
            except Offline:
                pass  # DuckDuckGo, as before
        return self._duckduckgo(query, limit)

    def _searxng(self, query, limit):
        params = {"q": query, "format": "json", "language": "ru"}
        if re.search(r"новост", query, re.I):  # "IT новости": the news engines first (Google News), else the web
            news = self._searxng_results(dict(params, categories="news"), limit)
            if len(news) >= 2:
                return news
        return self._searxng_results(params, limit)

    def _searxng_results(self, params, limit):
        data = self.web.get_json(self.web.searxng + "/search", params)
        results = []
        for r in data.get("results", []):
            site = re.sub(r"^www\.", "", urlsplit(r.get("url", "")).hostname or "")
            title = " ".join((r.get("title") or "").split())
            if title and site:
                results.append(Result(title, " ".join((r.get("content") or "").split()), site))
            if len(results) >= limit:
                break
        return results

    def _duckduckgo(self, query, limit):
        page = self.web.get(DDG, {"q": query, "kl": "ru-ru"})
        if "anomaly" in page.lower() and "result__a" not in page:
            raise Offline("поисковик просит капчу")
        results = []
        for block in re.split(r'<div class="result results_links', page)[1:]:
            if "result--ad" in block[:200]:
                continue
            link = re.search(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
            if not link:
                continue
            target = parse_qs(urlsplit(html.unescape(link.group(1))).query).get("uddg", [""])[0]
            snippet = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.S)
            text = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", "", s)).split())  # noqa: E731
            site = re.sub(r"^www\.", "", urlsplit(target).hostname or "")
            results.append(Result(text(link.group(2)), text(snippet.group(1)) if snippet else "", site))
            if len(results) >= limit:
                break
        return results
