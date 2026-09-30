"""Weather and search, with the services' answers made up here: no network in the tests."""

from datetime import datetime, timedelta

import pytest

from orpheus.brain import Brain
from orpheus.config import Config
from orpheus.memory import Memory
from orpheus.numbers import speakable
from orpheus.search import Search, clean_summary
from orpheus.weather import Weather
from orpheus.web import HOSTS, Offline, Web
from test_brain import FakeLLM, text, tool

NOW = datetime(2026, 9, 25, 14, 3)  # a Friday
DAYS = [(NOW.date() + timedelta(days=i)).isoformat() for i in range(7)]


def forecast(codes=(2, 61, 3, 0, 0, 71, 0), rain=(10, 80, 30, 0, 0, 60, 0), cur_t=13.3, feel=12.4, wind=2.2):
    hourly_t, hourly_p, hourly_c = [], [], []
    for d, code, p in zip(DAYS, codes, rain):
        for h in range(24):
            hourly_t.append("%sT%02d:00" % (d, h))
            wet = code in (61, 71) and 12 <= h < 21
            hourly_p.append(p if wet else 0)
            hourly_c.append(code if wet else 3)
    return {
        "current": {"temperature_2m": cur_t, "apparent_temperature": feel, "weather_code": 2, "wind_speed_10m": wind},
        "daily": {"time": DAYS, "weather_code": list(codes), "temperature_2m_max": [16.2, 17.0, 15.4, 12, 11, -1.2, 5],
                  "temperature_2m_min": [8.1, 9.6, 7.7, 5, 4, -6.4, 0], "precipitation_probability_max": list(rain),
                  "precipitation_sum": [0, 4, 0.2, 0, 0, 3, 0], "wind_speed_10m_max": [4, 6, 3, 2, 2, 5, 3],
                  "sunrise": ["%sT06:40" % d for d in DAYS], "sunset": ["%sT18:45" % d for d in DAYS]},
        "hourly": {"time": hourly_t, "precipitation_probability": hourly_p, "weather_code": hourly_c},
    }


PLACES = {
    "Сочи": [{"name": "Сочи", "latitude": 43.6, "longitude": 39.7, "population": 327608, "feature_code": "PPLA2"}],
    "Казан": [{"name": "Казань", "latitude": 55.8, "longitude": 49.1, "population": 1243500, "feature_code": "PPLA"}],
    "Каза": [{"name": "Казахстан", "latitude": 48, "longitude": 68, "population": 19000000, "feature_code": "PCLI"}],
    "Нижне": [], "Нижн": [{"name": "Нижний Новгород", "latitude": 56.3, "longitude": 44.0, "population": 1259013,
                          "feature_code": "PPLA"},
                         {"name": "Нижний Тагил", "latitude": 57.9, "longitude": 59.9, "population": 381116,
                          "feature_code": "PPL"}],
    "Санкт-Петербург": [{"name": "Санкт-Петербург", "latitude": 59.9, "longitude": 30.3, "population": 5351935,
                         "feature_code": "PPLA"}],
}

DDG_PAGE = """<html><div class="result results_links results_links_deep web-result result--ad">
<a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Реклама</a></div>
<div class="result results_links results_links_deep web-result ">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.cbr.ru%2Fcurrency_base%2F&amp;rut=x">Официальные <b>курсы</b> валют</a>
<a class="result__snippet" href="x">ЦБ установил с 26.09.2026 курс доллара 81,23 руб.</a></div>
<div class="result results_links results_links_deep web-result ">
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fbankiros.ru%2Fcurrency%2Fusd&amp;rut=y">Курс доллара на сегодня</a>
<a class="result__snippet" href="y">Курс доллара ЦБ РФ на сегодня и завтра к рублю.</a></div></html>"""

CBR_XML = ('<?xml version="1.0" encoding="windows-1251"?><ValCurs Date="25.09.2026" name="Foreign Currency Market">'
           '<Valute ID="R01235"><NumCode>840</NumCode><CharCode>USD</CharCode><Nominal>1</Nominal><Name>Доллар США</Name>'
           '<Value>84,3414</Value><VunitRate>84,3414</VunitRate></Valute>'
           '<Valute ID="R01239"><NumCode>978</NumCode><CharCode>EUR</CharCode><Nominal>1</Nominal><Name>Евро</Name>'
           '<Value>98,6107</Value><VunitRate>98,6107</VunitRate></Valute>'
           '<Valute ID="R01820"><NumCode>392</NumCode><CharCode>JPY</CharCode><Nominal>100</Nominal><Name>Японских иен</Name>'
           '<Value>55,1234</Value><VunitRate>0,551234</VunitRate></Valute></ValCurs>')

EMPTY = [{"message": {"content": ""}}, {"done": True, "eval_count": 25}]  # a call Ollama could not parse and dropped

WIKI = {"query": {"pages": {
    "1": {"index": 1, "title": "Тесла, Никола", "extract": "Ни́кола Те́сла (серб. Никола Тесла; 10 июля 1856, Смилян — 7 января "
          "1943, Нью-Йорк) — американский инженер сербского происхождения, изобретатель в области электротехники. "
          "Известен вкладом в создание устройств на переменном токе. Третье предложение."},
    "2": {"index": 2, "title": "Tesla", "extract": "Tesla — компания."}}}}


class FakeWeb:
    def __init__(self):
        self.calls = []
        self.down = False
        self.forecast = forecast()
        self.cbr = CBR_XML

    def _check(self, url):
        self.calls.append(url)
        if self.down:
            raise Offline("нет сети")

    def get_json(self, url, params=None):
        self._check(url)
        self.params = getattr(self, "params", []) + [params]
        if "geocoding" in url:
            return {"results": next((v for k, v in PLACES.items() if k.lower() == params["name"].lower()), [])}
        if "open-meteo" in url:
            return self.forecast
        if "wikipedia" in url:
            return WIKI if "есла" in params["gsrsearch"] else {"query": {"pages": {}}}
        raise AssertionError(url)

    def get(self, url, params=None, headers=None):
        self._check(url)
        self.params = getattr(self, "params", []) + [params]
        if "cbr.ru" in url:
            self.cbr_params = params
            return self.cbr
        return DDG_PAGE


def make(replies=(), personal=False, planner=None):
    web = FakeWeb()
    clock = [0.0]
    weather = Weather(web, clock=lambda: clock[0], now=lambda: NOW)
    brain = Brain(Config(), Memory(":memory:"), FakeLLM(*replies), private=Memory(":memory:"),
                  weather=weather, search=Search(web), planner=planner)
    if personal:
        brain.set_personal(True)
    brain.web, brain.clock_ = web, clock
    return brain


def say(brain, phrase):
    return "".join(brain.ask(phrase, now=NOW))


# ------------------------------------------------------------------------------------ weather

def test_weather_now_and_by_day():
    brain = make()
    assert say(brain, "Какая погода?") == "Сейчас в Москве +13, переменная облачность, ветер 2 м/с."
    assert say(brain, "Какая погода завтра?") == "Завтра в Москве от +10 до +17, небольшой дождь, вероятность осадков 80%."
    assert say(brain, "Какая погода сегодня?") == "Сегодня в Москве от +8 до +16, переменная облачность; сейчас +13."
    assert say(brain, "Прогноз погоды на выходные") == ("В Москве: завтра от +10 до +17, небольшой дождь, вероятность "
                                                        "осадков 80%. Послезавтра от +8 до +15, пасмурно, вероятность осадков 30%.")
    assert say(brain, "Сколько градусов на улице?") == "Сейчас в Москве +13."
    assert brain.llm.requests == []


def test_rain_snow_and_clothes():
    brain = make()
    assert say(brain, "Будет ли завтра дождь?") == "Да, завтра ожидается дождь: вероятность 80%, днём и вечером."
    assert say(brain, "Нужен ли зонт в понедельник?") == "Нет, в понедельник дождя не ожидается."
    assert say(brain, "Пойдёт ли снег в среду?") == "Да, в среду ожидается снег: вероятность 60%, днём и вечером."
    assert say(brain, "Как одеться завтра?") == "Завтра в Москве, ощущается как +17: лёгкая куртка или свитер, и возьми зонт."


def test_other_cities_whatever_their_case():
    brain = make()
    assert say(brain, "Какая погода в Сочи?").startswith("Сейчас в Сочи +13")
    assert say(brain, "Погода в Казани завтра").startswith("Завтра в Казани от")  # "Казан" -> Казань, not Казахстан
    assert say(brain, "Какая погода в Нижнем Новгороде?").startswith("Сейчас в Нижнем Новгороде")
    assert say(brain, "Какая погода в Питере?").startswith("Сейчас в Питере")
    assert say(brain, "Какая погода в Абракадабрске?") == "Такого города я не нашёл."


def test_the_forecast_is_kept_and_survives_a_short_break():
    brain = make()
    say(brain, "Какая погода?")
    n = len(brain.web.calls)
    say(brain, "Какая погода завтра?")
    assert len(brain.web.calls) == n  # from the cache
    brain.clock_[0] = 3 * 3600
    brain.web.down = True
    assert say(brain, "Какая погода?").startswith("Сейчас в Москве")  # stale but kept
    brain.clock_[0] = 10 * 3600
    assert say(brain, "Какая погода?").startswith("Сейчас в Москве")  # the home one is kept until collected again
    assert say(brain, "Какая погода в Сочи?") == "Погоду сейчас не получить: нет связи с интернетом."


def test_the_home_forecast_is_collected_every_6_hours_and_kept_on_disk(tmp_path):
    from orpheus.weather import HOME_REFRESH, MOSCOW
    web, wall = FakeWeb(), [1000.0]
    store = tmp_path / "weather.json"
    w = Weather(web, home=MOSCOW, now=lambda: NOW, store=store, wall=lambda: wall[0])
    assert w.refresh_home() == HOME_REFRESH  # nothing yet: fetched at once
    assert len(web.calls) == 1 and store.exists()
    wall[0] += 3600
    assert w.refresh_home() == HOME_REFRESH - 3600 and len(web.calls) == 1  # not due yet
    # after a restart: read from disk, not fetched again
    w2 = Weather(web, home=MOSCOW, now=lambda: NOW, store=store, wall=lambda: wall[0])
    assert w2.now_text(w2.home).startswith("Сейчас в Москве") and len(web.calls) == 1
    wall[0] += HOME_REFRESH
    assert w2.refresh_home() == HOME_REFRESH and len(web.calls) == 2  # 6 hours on: collected again
    params = web.params[-1]
    assert params["forecast_days"] == 8 and "temperature_2m" in params["hourly"]


def test_now_is_read_off_the_hour_and_parts_of_the_day_from_hours():
    brain = make()
    brain.web.forecast["hourly"]["temperature_2m"] = [5.0 + (i % 24) / 2 for i in range(24 * 7)]
    for k in ("apparent_temperature", "wind_speed_10m"):
        brain.web.forecast["hourly"][k] = [3.0] * (24 * 7)
    assert say(brain, "Какая погода?").startswith("Сейчас в Москве +12")  # 14:00 -> 5 + 7
    assert say(brain, "Какая погода вечером?") == "Сегодня вечером в Москве от +14 до +16, пасмурно."
    assert say(brain, "Какая погода завтра утром?") == \
        "Завтра утром в Москве от +8 до +10, пасмурно."


def test_a_word_said_twice_after_its_stresses_are_gone_is_said_once():
    assert clean_summary("Эмпа́тия или эмпати́я — осознанное сопереживание.") == "Эмпатия — осознанное сопереживание."
    assert clean_summary("Кот или кошка — животное.") == "Кот или кошка — животное."


def test_weather_is_spoken_right():
    assert speakable("Сейчас в Москве +13, переменная облачность, ветер 2 м/с.") == \
        "Сейчас в Москве плюс тринадцать, переменная облачность, ветер два метра в секунду."
    assert speakable("от -6 до -1, вероятность осадков 60%") == "от минус шесть до минус один, вероятность осадков шестьдесят процентов"
    assert speakable("с 10 - 11") == "с десяти - одиннадцать"


# ------------------------------------------------------------------------------------ Wikipedia

def test_who_is_answered_from_wikipedia_without_the_model():
    brain = make()
    assert say(brain, "Кто такой Никола Тесла?") == \
        "Никола Тесла — американский инженер сербского происхождения, изобретатель в области электротехники."  # one, aloud
    assert brain.llm.requests == [] and brain.handled == "wiki"


def test_nothing_sure_in_wikipedia_goes_to_the_model():
    brain = make(replies=[text("Это слово я не знаю.")])
    assert say(brain, "Что такое абракадабра?") == "Это слово я не знаю."
    assert brain.handled == "модель"


def test_someone_from_the_memory_is_the_models():
    brain = make(replies=[text("Ваш коллега.")])
    brain.memory.remember("Никола Тесла — мой коллега по работе")
    assert say(brain, "Кто такой Никола Тесла?") == "Ваш коллега."


def test_summary_cleaning():
    assert clean_summary("Ни́кола Те́сла (серб. Тесла (Tesla); 1856) — инженер [1]. Второе. Третье.") == \
        "Никола Тесла — инженер. Второе."


# ------------------------------------------------------------------------------------ search

def test_search_says_first_then_answers_from_the_results():
    brain = make(replies=[text("По данным cbr.ru, 81,23 рубля.")])
    pieces = list(brain.ask("Найди в интернете курс биткоина", now=NOW))
    assert pieces[0] == "Сейчас поищу."
    assert "".join(pieces[1:]) == " По данным cbr.ru, 81,23 рубля."
    sent = brain.llm.requests[0][-1]["content"]
    assert "найдено в интернете по запросу «курс биткоина»" in sent
    assert "1) Официальные курсы валют — ЦБ установил с 26.09.2026 курс доллара 81,23 руб. (cbr.ru)" in sent
    assert "Реклама" not in sent


def test_search_failures_and_the_personal_section():
    brain = make(replies=[text("Борщ варят так: свёкла, капуста, картофель.")])
    brain.web.down = True
    assert say(brain, "Поищи в интернете рецепт борща") == \
        "Сейчас поищу. Поиск сейчас не отвечает. Борщ варят так: свёкла, капуста, картофель."
    assert "поиск в интернете не ответил" in brain.llm.requests[0][-1]["content"]  # then what it knows for long
    brain = make(replies=[text("Я не могу найти это в интернете прямо сейчас, но по памяти — Толстой.")])
    brain.web.down = True
    say(brain, "Найди в интернете, кто написал «Войну и мир».")
    assert len(brain.llm.requests) == 1  # its "не могу найти" after a search that failed: not searched once more
    brain = make(personal=True)
    assert say(brain, "Найди в интернете рецепт борща") == "В личном разделе я не ищу в интернете."
    assert brain.web.calls == []


def test_the_model_can_search_too():
    brain = make(replies=[[{"message": {"content": "", "tool_calls": [{"function": {"name": "web_search",
                                                                                     "arguments": {"query": "курс доллара"}}}]}},
                           {"done": True}], text("81,23 рубля, по данным cbr.ru.")])
    assert say(brain, "А доллар нынче почём?") == "81,23 рубля, по данным cbr.ru."
    assert "cbr.ru" in brain.llm.requests[1][-1]["content"]  # the results reached the model
    # and then left the history: only the question and the answer are carried on
    assert [(m["role"], m["content"]) for m in brain.history] == [("user", "А доллар нынче почём?"),
                                                                  ("assistant", "81,23 рубля, по данным cbr.ru.")]


# ------------------------------------------------------------------------------------ the way out

def test_only_the_known_hosts_and_only_through_the_proxy():
    web = Web("http://127.0.0.1:2080")
    with pytest.raises(ValueError):
        web.get("https://example.com/")
    with pytest.raises(ValueError):
        web.get("http://ru.wikipedia.org/")  # not https
    handlers = [h for h in web.opener.handlers if type(h).__name__ == "ProxyHandler"]
    assert handlers[0].proxies == {"http": "http://127.0.0.1:2080", "https": "http://127.0.0.1:2080"}
    assert all(h.proxies == {} for h in Web("").opener.handlers if type(h).__name__ == "ProxyHandler")
    assert HOSTS == {"api.open-meteo.com", "geocoding-api.open-meteo.com", "ru.wikipedia.org", "html.duckduckgo.com",
                     "www.cbr.ru"}


def test_a_dead_proxy_is_offline_not_a_way_around(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "")
    web = Web("http://127.0.0.1:9", timeout=2)  # nothing listens there
    with pytest.raises(Offline):
        web.get_json("https://api.open-meteo.com/v1/forecast", {"latitude": 1})


def test_rain_over_several_days_and_wiki_about_the_thing_itself():
    brain = make()
    assert say(brain, "Будет ли дождь на выходных?") == ("Да, завтра ожидается дождь: вероятность 80%, днём и вечером. "
                                                         "Возможен дождь: послезавтра вероятность осадков 30%.")
    assert say(brain, "Будет ли дождь в понедельник?") == "Нет, в понедельник дождя не ожидается."
    WIKI["query"]["pages"]["0"] = {"index": 0, "title": "Потомки Теслы", "extract": "У Теслы не было детей, но были потомки."}
    try:
        assert say(brain, "Кто такой Никола Тесла?").startswith("Никола Тесла — американский инженер")
    finally:
        del WIKI["query"]["pages"]["0"]


def test_who_prefers_the_article_about_a_person():
    brain = make()
    WIKI["query"]["pages"]["0"] = {"index": 0, "title": "Тесла (род)", "extract": "Теслы — древний род из Сербии."}
    try:
        assert say(brain, "Кто такой Тесла?").startswith("Никола Тесла — американский инженер")
    finally:
        del WIKI["query"]["pages"]["0"]


# ------------------------------------------------------------------------------------ conversations

def test_weather_follow_ups_and_two_cities():
    brain = make()
    say(brain, "Какая погода в Казани?")
    assert say(brain, "А завтра?") == "Завтра в Казани от +10 до +17, небольшой дождь, вероятность осадков 80%."
    assert say(brain, "А в Сочи?") == "Завтра в Сочи от +10 до +17, небольшой дождь, вероятность осадков 80%."
    assert say(brain, "Ага, в Казани.") == "Завтра в Казани от +10 до +17, небольшой дождь, вероятность осадков 80%."  # as heard
    assert say(brain, "А дождь будет?") == "Да, завтра в Казани ожидается дождь: вероятность 80%, днём и вечером."
    assert say(brain, "Какая погода в Москве и в Питере?") == ("Сейчас в Москве +13, переменная облачность, ветер 2 м/с. "
                                                               "Сейчас в Питере +13, переменная облачность, ветер 2 м/с.")
    assert say(brain, "Сколько градусов и нужен ли зонт?") == "Сейчас в Москве +13. Нет, сегодня дождя не ожидается."
    assert say(brain, "какая погода в питере завтра").startswith("Завтра в Питере от +10")
    assert say(brain, "Какая погода в деревне?").startswith("Сейчас в Москве")  # said small and no such city: home
    assert brain.llm.requests == []


def test_the_model_has_the_weather_as_a_tool():
    brain = make(replies=[tool("weather", when="завтра", city="Казань"), text("Завтра дождь, возьмите зонт.")])
    assert say(brain, "Если завтра в Казани дождь, я не поеду. Что скажешь?") == "Завтра дождь, возьмите зонт."
    assert brain.history[-2] == {"role": "tool", "tool_name": "weather",
                                 "content": "Завтра в городе Казань от +10 до +17, небольшой дождь, вероятность осадков 80%."}


def test_what_the_program_found_comes_with_the_phrase_and_goes_after_it():
    brain = make(replies=[text("По данным cbr.ru, 81,23 рубля.")])
    say(brain, "Найди в интернете курс биткоина")
    sent = brain.llm.requests[0][-1]["content"]
    assert sent.startswith("Найди в интернете курс биткоина\n[найдено в интернете по запросу «курс биткоина»")
    assert brain.history[-2] == {"role": "user", "content": "Найди в интернете курс биткоина"}  # the results not read again


def test_an_answer_cut_by_the_limit_is_not_heard_half_way():
    cut = [{"message": {"content": p}} for p in ["По данным cbr.ru, ", "81,23 рубля. ", "А ещё евро по"]] + \
          [{"done": True, "done_reason": "length"}]
    brain = make(replies=[cut])
    assert say(brain, "Найди в интернете курс биткоина") == "Сейчас поищу. По данным cbr.ru, 81,23 рубля."


def test_a_fact_to_look_up_is_searched_not_read_from_an_article():
    brain = make(replies=[text("Борис Ельцин.")])
    assert say(brain, "Кто был первым президентом России?") == "Сейчас поищу. Борис Ельцин."
    assert brain.skills.handled == "wiki"
    assert brain.web.calls[-1].startswith("https://html.duckduckgo.com")


def test_what_the_model_cannot_know_is_searched_at_once():
    brain = make(replies=[text("81,23 рубля, по данным cbr.ru.")])
    assert say(brain, "Какой сейчас курс биткоина?") == "Сейчас поищу. 81,23 рубля, по данным cbr.ru."
    assert brain.skills.handled == "fresh"
    assert brain.web.calls[-1].startswith("https://html.duckduckgo.com")
    assert "биткоин" in brain.web.params[-1]["q"].lower()  # the question itself is what was searched
    brain = make(personal=True)
    assert say(brain, "Какие новости?") == "В личном разделе я не ищу в интернете."


def test_a_reminder_on_rain_looks_at_the_forecast_first():
    from test_planner import tools
    brain = make(planner=tools())
    reply = say(brain, "Если завтра будет дождь, напомни взять зонт.")
    assert reply.startswith("Да, завтра ожидается дождь: вероятность 80%, днём и вечером. Добавил на завтра")
    assert brain.planner.planner.quick_text == "завтра взять зонт"
    assert say(brain, "Если в понедельник будет дождь, напомни взять зонт.") == \
        "Нет, в понедельник дождя не ожидается. Так что напоминание не ставлю."


def test_a_date_is_not_the_end_of_a_sentence_cut_by_the_limit():
    cut = [{"message": {"content": p}} for p in ["Курс есть на cbr.ru. ", "Точного курса на 26.09."]] + \
          [{"done": True, "done_reason": "length"}]
    brain = make(replies=[cut])
    assert say(brain, "Найди в интернете курс биткоина") == "Сейчас поищу. Курс есть на cbr.ru."


def test_the_model_gets_no_forecast_with_the_phrase():
    brain = make(replies=[text("Смотря куда.")])
    say(brain, "Стоит ли в субботу ехать за город, будет тепло?")
    assert "прогноз погоды" not in brain.llm.requests[0][-1]["content"]


def test_what_the_weather_knows_more_and_follow_ups_by_parts_of_the_day():
    brain = make()
    data = brain.web.forecast
    n = len(data["hourly"]["time"])
    data["hourly"].update(temperature_2m=[10.0] * n, apparent_temperature=[9.0] * n, wind_speed_10m=[3.0] * n,
                          relative_humidity_2m=[70] * n)
    data["hourly"]["weather_code"] = [95 if t.startswith(DAYS[1]) and 15 <= int(t[11:13]) < 17 else c
                                      for t, c in zip(data["hourly"]["time"], data["hourly"]["weather_code"])]
    assert say(brain, "Какая погода будет вечером?") == "Сегодня вечером в Москве +10, пасмурно."
    assert say(brain, "А ночью?") == "Сегодня ночью в Москве +10, пасмурно."  # the night after today, its hours tomorrow's
    assert say(brain, "Будет ли гроза завтра?") == "Да, возможна гроза: завтра днём."
    assert say(brain, "Будет ли гроза сегодня?") == "Нет, сегодня грозы не ожидается."
    assert say(brain, "Какая влажность?") == "Сейчас в Москве влажность 70%."
    assert say(brain, "Во сколько сегодня закат?") == "Сегодня в Москве закат в 18:45."
    assert say(brain, "Когда завтра рассвет?") == "Завтра в Москве восход в 06:40."
    assert say(brain, "Во сколько восход и закат?") == "Сегодня в Москве восход в 06:40, закат в 18:45."
    assert say(brain, "Какой ветер?") == "Сейчас в Москве ветер 3 м/с."
    assert say(brain, "Какой ветер завтра?") == "Завтра в Москве ветер до 6 м/с."
    assert say(brain, "Нужна ли куртка завтра?").startswith("Завтра в Москве, ощущается как")
    assert say(brain, "Нужна ли куртка завтра утром?") == "Завтра утром в Москве, ощущается как +9: тёплая куртка."


def test_a_reminder_on_no_rain_is_the_other_way_round():
    from test_planner import tools
    brain = make(planner=tools())
    assert say(brain, "Если в понедельник не будет дождя, добавь пикник в 12.").startswith(
        "Нет, в понедельник дождя не ожидается. Добавил на")
    assert say(brain, "Если завтра не будет дождя, добавь пикник в 12.") == \
        "Да, завтра ожидается дождь: вероятность 80%, днём и вечером. Так что не добавляю."


def test_a_search_promised_and_not_called_is_made_by_the_program():
    brain = make(replies=[text("Позвольте мне поискать информацию."), text("Около 6%, по данным cbr.ru.")])
    reply = say(brain, "Сколько людей живёт в Бразилии?")
    assert reply == "Позвольте мне поискать информацию. Около 6%, по данным cbr.ru."
    assert brain.calls == [("web_search", {"query": "Сколько людей живёт в Бразилии"})]
    assert any("найдено в интернете" in m["content"] for m in brain.llm.requests[1] if m["role"] == "tool")
    # the call alone in the history, as when the model makes it: after its own promise it said nothing more
    assert {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "web_search", "arguments": {"query": "Сколько людей живёт в Бразилии"}}}]} in brain.llm.requests[1]


def test_who_won_is_searched_by_the_program_whatever_the_asking_words():
    brain = make(replies=[text("Выиграла Испания, по данным fifa.com.")])
    assert say(brain, "Выясни, кто выиграл последний чемпионат мира по футболу.") == \
        "Сейчас поищу. Выиграла Испания, по данным fifa.com."
    assert brain.skills.handled == "fresh"


def test_a_call_lost_on_the_way_is_a_search_made_by_the_program():
    brain = make(replies=[EMPTY, text("Около 6%, по данным cbr.ru.")])
    assert say(brain, "Сколько людей живёт в Бразилии?") == "Около 6%, по данным cbr.ru."
    assert brain.calls == [("web_search", {"query": "Сколько людей живёт в Бразилии"})]


def test_a_search_said_impossible_without_trying_is_made():
    brain = make(replies=[text("Я не могу найти это в интернете прямо сейчас."), text("Нашлось: около 6%.")])
    assert say(brain, "Сколько людей живёт в Бразилии?") == "Я не могу найти это в интернете прямо сейчас. Нашлось: около 6%."
    assert brain.calls[0][0] == "web_search"


def test_an_empty_answer_after_the_results_is_asked_for_once_more():
    brain = make(replies=[EMPTY, EMPTY, text("Около 6%.")])
    assert say(brain, "Сколько людей живёт в Бразилии?") == "Около 6%."
    assert "ответь по тому, что нашлось" in brain.llm.requests[2][-1]["content"]
    assert not any("ответь по тому" in str(m.get("content")) for m in brain.history)  # asked, then taken out
    brain = make(replies=[text("Сейчас найду: около 6%.")])
    say(brain, "Найди в интернете инфляцию в России.")  # found by the program: no second search on a promise
    assert brain.calls == [("web_search", {"query": "инфляцию в России"})]


def test_a_follow_up_to_a_search_is_searched_with_what_it_was_about():
    brain = make(replies=[text("Выйдет 19 ноября 2026 года."), text("Около 70 долларов.")])
    say(brain, "Найди в интернете, когда выйдет GTA 6.")
    assert say(brain, "А сколько она будет стоить?") == "Сейчас поищу. Около 70 долларов."
    assert brain.calls == [("web_search", {"query": "когда выйдет GTA 6 сколько она будет стоить"})]


def test_exchange_rates_are_the_central_banks_said_by_the_program():
    brain = make()
    assert say(brain, "Какой курс доллара?") == "По курсу ЦБ доллар — 84 рубля 34 копейки."
    assert brain.web.cbr_params == {"date_req": "25/09/2026"}
    assert say(brain, "А евро?") == "По курсу ЦБ евро — 98 рублей 61 копейка."
    assert say(brain, "Какой курс доллара и евро?") == "По курсу ЦБ доллар — 84 рубля 34 копейки; евро — 98 рублей 61 копейка."
    assert say(brain, "Сколько будет 100 долларов в рублях?") == "100 долларов — 8 434 рубля по курсу ЦБ."
    assert say(brain, "Сколько долларов на 10000 рублей?") == "10 000 рублей — примерно 119 долларов по курсу ЦБ."
    assert say(brain, "Курс иены") == "По курсу ЦБ 100 иен — 55 рублей 12 копеек."
    assert say(brain, "Найди в интернете курс евро.") == "По курсу ЦБ евро — 98 рублей 61 копейка."
    assert say(brain, "Переведи 50 евро в рубли.") == "50 евро — 4 931 рубль по курсу ЦБ."
    assert brain.llm.requests == []
    assert sum("cbr.ru" in u for u in brain.web.calls) == 1  # kept for an hour
    assert speakable("84 рубля 34 копейки; 100 иен — 55 рублей 12 копеек") == \
        "восемьдесят четыре рубля тридцать четыре копейки; сто иен — пятьдесят пять рублей двенадцать копеек"


def test_the_rate_for_tomorrow_only_once_it_is_set_and_not_a_currency_is_searched():
    from orpheus.skills import rate_in_force
    brain = make(replies=[text("Около 84 тысяч долларов.")])
    assert say(brain, "Какой курс доллара на завтра?") == "Курс ЦБ на завтра ещё не установлен. Сейчас доллар — 84 рубля 34 копейки."
    assert brain.web.cbr_params == {"date_req": "26/09/2026"}
    brain.web.cbr = CBR_XML.replace("25.09.2026", "26.09.2026")
    brain.search._rates.clear()
    assert say(brain, "Какой курс доллара на завтра?") == "Курс ЦБ на завтра: доллар — 84 рубля 34 копейки."
    sat = datetime(2026, 9, 26).date()
    assert rate_in_force(sat, sat + timedelta(days=2)) and not rate_in_force(sat, sat + timedelta(days=3))  # through Monday
    assert say(brain, "Какой курс биткоина?") == "Сейчас поищу. Около 84 тысяч долларов."
    brain = make()
    brain.web.down = True
    assert say(brain, "Какой курс евро?") == "Курс сейчас не получить: ЦБ не отвечает."


def test_a_search_offered_is_made_on_a_yes():
    brain = make(replies=[text("Я могу поискать это в интернете. Хотите?"), text("Около 6%, по данным cbr.ru.")])
    assert say(brain, "Сколько людей живёт в Бразилии?") == "Я могу поискать это в интернете. Хотите?"
    assert brain.calls == []  # asked, not done
    assert say(brain, "Да.") == "Сейчас поищу. Около 6%, по данным cbr.ru."
    assert brain.calls == [("web_search", {"query": "Сколько людей живёт в Бразилии"})]
    brain = make(replies=[text("Могу поискать в интернете, хотите?")])
    say(brain, "Сколько людей живёт в Бразилии?")
    assert say(brain, "Нет.") == "Хорошо, не ищу."


def test_what_is_now_is_searched_but_not_what_is_mine():
    brain = make(replies=[text("Около 6%, по данным cbr.ru.")])
    assert say(brain, "Какая сейчас инфляция в России?") == "Сейчас поищу. Около 6%, по данным cbr.ru."
    assert brain.skills.handled == "fresh"
    brain = make(replies=[text("Не знаю.")])
    say(brain, "Какая сейчас у меня задача?")
    assert brain.calls == [] and brain.web.calls == []


def test_look_at_the_weather_and_say_whether_to_take_an_umbrella_is_two_requests_of_the_program():
    brain = make()
    assert say(brain, "Посмотри погоду на завтра и скажи, брать ли зонт.") == (
        "Завтра в Москве от +10 до +17, небольшой дождь, вероятность осадков 80%. "
        "Да, завтра ожидается дождь: вероятность 80%, днём и вечером.")
    assert brain.llm.requests == []


def test_a_request_hung_on_the_way_is_tried_once_more():
    import socket
    import urllib.error

    class Opener:
        def __init__(self, *failures):
            self.failures, self.timeouts = list(failures), []

        def open(self, req, timeout):
            self.timeouts.append(timeout)
            if self.failures:
                raise self.failures.pop(0)

            class Answer:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return b"{}"
            return Answer()

    web = Web("http://127.0.0.1:2080")
    web.opener = Opener(urllib.error.URLError(socket.timeout("timed out")))
    assert web.get("https://ru.wikipedia.org/w/api.php") == "{}" and web.opener.timeouts == [4, 8]
    web.opener = Opener(urllib.error.HTTPError("https://ru.wikipedia.org/", 429, "Too Many Requests", {}, None))
    with pytest.raises(Offline):
        web.get("https://ru.wikipedia.org/w/api.php")
    assert web.opener.timeouts == [4]  # an answer, even a refusal, is not asked for again
    web.opener = Opener(TimeoutError(), TimeoutError())
    with pytest.raises(Offline):
        web.get("https://ru.wikipedia.org/w/api.php")
    assert web.opener.timeouts == [4, 8]


def test_a_search_offered_on_a_condition_is_not_made():
    brain = make(replies=[text("Если вы назовёте предмет, я могу поискать полезные материалы.")])
    say(brain, "Какой предмет мне стоит подтянуть к экзамену?")
    assert brain.calls == [] and len(brain.llm.requests) == 1
    brain = make(replies=[text("Я не могу найти это в интернете прямо сейчас."), text("Нашлось.")])
    say(brain, "Сколько людей живёт в Бразилии?")  # "не могу" is no offer: searched
    assert brain.calls[0][0] == "web_search"


def test_searxng_first_and_duckduckgo_when_it_fails():
    web = FakeWeb()
    web.searxng = "http://127.0.0.1:8888"
    answers = {"results": [{"title": "Президент США — Википедия", "content": "Действующий президент — Дональд Трамп.",
                            "url": "https://ru.wikipedia.org/wiki/x"},
                           {"title": "Без адреса", "content": "", "url": ""}]}
    real = web.get_json

    def get_json(url, params=None):
        if url.startswith("http://127.0.0.1:8888/"):
            web.calls.append(url)
            if web.down_searxng:
                raise Offline("нет SearXNG")
            assert params == {"q": "кто президент", "format": "json", "language": "ru"}
            return answers
        return real(url, params)
    web.get_json, web.down_searxng = get_json, False
    found = Search(web).web_results("кто президент")
    assert [(r.title, r.site) for r in found] == [("Президент США — Википедия", "ru.wikipedia.org")]
    assert not any("duckduckgo" in u for u in web.calls)
    web.down_searxng = True
    assert Search(web).web_results("кто президент")[0].site == "cbr.ru"  # DuckDuckGo's page in the fake


def test_searxng_only_on_this_laptop_and_asked_without_the_proxy():
    web = Web("http://127.0.0.1:2080", searxng="http://127.0.0.1:8888")
    assert web.local is not web.opener
    with pytest.raises(ValueError):
        Web("http://127.0.0.1:2080", searxng="http://example.com:8888")
    with pytest.raises(ValueError):
        web.get("http://127.0.0.1:9999/other")


def test_news_are_looked_for_among_the_news_first():
    web = FakeWeb()
    web.searxng = "http://127.0.0.1:8888"
    asked = []

    def get_json(url, params=None):
        asked.append(params.get("categories"))
        news = [{"title": "ИТ-рынок", "content": "Рост", "url": "https://rbc.ru/a"},
                {"title": "ИИ", "content": "Новое", "url": "https://habr.com/b"}]
        return {"results": news if params.get("categories") == "news" else []}
    web.get_json = get_json
    assert [r.site for r in Search(web).web_results("последние IT новости")] == ["rbc.ru", "habr.com"]
    assert asked == ["news"]


def test_news_on_a_topic_are_searched_at_once():
    for phrase in ("Расскажи последние IT новости.", "Какие новости про космос?", "Какие спортивные новости?"):
        brain = make(replies=[text("Вот что нашлось.")])
        assert say(brain, phrase) == "Сейчас поищу. Вот что нашлось."
        assert brain.skills.handled == "fresh", phrase


def test_the_day_summary_has_the_weather_at_home():
    brain = make()
    assert say(brain, "Доброе утро.") == ("Добрый день! Сегодня пятница, 25 сентября. "
                                         "Сегодня в Москве от +8 до +16, переменная облачность; сейчас +13.")


def test_news_after_a_reaction_and_then_a_topic_alone_are_searched():
    # a talk by the buds: "Забавно." before the request made it the model's, and it searched nothing;
    # then it asked for a topic, promised "Я найду информацию о блокировках VPN" and searched nothing again
    brain = make(replies=[text("Вот главное за сегодня."), text("Про блокировки VPN вот что.")])
    say(brain, "Забавно. Расскажи последние новости, которые ты нашёл в интернете.")
    assert brain.calls[-1][0] == "web_search" and "новости" in brain.calls[-1][1]["query"]
    say(brain, "Блокировки VPN.")
    assert brain.calls[-1] == ("web_search", {"query": "новости про Блокировки VPN"})


def test_find_information_promised_is_a_search_made():
    brain = make(replies=[text("Я найду информацию о блокировках VPN прямо сейчас."), text("Вот что нашлось.")])
    reply = say(brain, "Что с блокировками VPN?")
    assert reply.endswith("Вот что нашлось.")
    assert brain.calls[-1][0] == "web_search"


def test_news_about_a_topic_without_asking_words():
    for phrase in ("Новости про блокировки VPN", "Что нового про Starship?", "Что нового в интернете?"):
        brain = make(replies=[text("Вот.")])
        say(brain, phrase)
        assert brain.calls and brain.calls[-1][0] == "web_search", phrase


def test_the_weather_city_goes_on_and_there_is_it():
    # the fourth talk: "а там дождь будет", "нужен ли зонт", "а ветер" after Kazan were answered for home, or by the model
    brain = make(replies=[text("Ясно.")] * 3)
    say(brain, "погода в Казани")
    assert say(brain, "а там дождь будет").endswith("в Казани дождя не ожидается.") or "в Казани" in brain.last_reply
    assert "в Казани" in say(brain, "нужен ли зонт")
    assert "в Казани" in say(brain, "а ветер сильный")
    assert say(brain, "в Казани сегодня сколько градусов") == "Сейчас в Казани +13."


def test_degrees_then_another_day_or_city_or_home_is_the_forecast():
    # the fifth talk: after "в Питере сегодня сколько градусов", "а завтра", "а в Твери", "а дома" were the model's
    # made-up numbers ("+15", "+18", "дома +13")
    brain = make(replies=[text("Ясно.")] * 3)
    assert say(brain, "в Казани сегодня сколько градусов") == "Сейчас в Казани +13."
    assert say(brain, "а завтра").startswith("Завтра в Казани от")
    assert say(brain, "как там в Казани").startswith("Сейчас в Казани")
    assert say(brain, "как погода в Казани").startswith("Сейчас в Казани")
    assert say(brain, "в Казани сейчас холодно") == "Сейчас в Казани +13."
