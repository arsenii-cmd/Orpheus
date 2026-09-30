"""The weather, from Open-Meteo (no key; through the proxy, see web.py).

The home city's forecast - hour by hour for today, day by day for the week after - is collected in
the background every 6 hours and kept on disk (weather.json), so "какая погода?" is answered at once,
after a restart too, and a break in the network goes unnoticed; "now" is read off the hour it is.
Another city is fetched when asked (~1 s) and kept for half an hour. Answers are said by the
program, without the model:

    "Сейчас в Москве +13, переменная облачность, ветер 2 м/с."
    "Завтра в Москве от +8 до +17, облачно, вероятность осадков 40%."

Numbers stay digits here: numbers.speakable() says them ("плюс тринадцать", "два метра в секунду").
"""

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from .web import Offline

FORECAST = "https://api.open-meteo.com/v1/forecast"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
REFRESH = 30 * 60  # another city's forecast is kept this long
HOME_REFRESH = 6 * 3600  # the home city's is collected this often
DAYS = 8  # today and the 7 days after it

# WMO weather codes as Open-Meteo gives them
CODES = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "туман с изморозью",
    51: "слабая морось", 53: "морось", 55: "сильная морось", 56: "ледяная морось", 57: "сильная ледяная морось",
    61: "небольшой дождь", 63: "дождь", 65: "сильный дождь", 66: "ледяной дождь", 67: "сильный ледяной дождь",
    71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
    80: "небольшой ливень", 81: "ливень", 82: "сильный ливень", 85: "небольшой снегопад", 86: "сильный снегопад",
    95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}
RAIN = set(range(51, 68)) | {80, 81, 82, 95, 96, 99}
THUNDER = {95, 96, 99}
SNOW = {71, 73, 75, 77, 85, 86}
ALIASES = {"питер": "Санкт-Петербург", "спб": "Санкт-Петербург", "петербург": "Санкт-Петербург",
           "мск": "Москва", "екб": "Екатеринбург", "нск": "Новосибирск"}
WEEKDAYS_ACC = ["в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье"]


@dataclass(frozen=True)
class Place:
    name: str
    where: str  # "в Москве": how it is said after the weather
    lat: float
    lon: float
    tz: str = "Europe/Moscow"  # the time zone, for "который час в Токио?"


MOSCOW = Place("Москва", "в Москве", 55.7558, 37.6173)
DOLGOPRUDNY = Place("Долгопрудный", "в Долгопрудном", 55.9386, 37.5101)
# the home city (ORPHEUS_CITY) is one of these; any other named in a phrase is found by the geocoder
KNOWN = {"москва": MOSCOW, "санкт-петербург": Place("Санкт-Петербург", "в Санкт-Петербурге", 59.9386, 30.3141),
         "казань": Place("Казань", "в Казани", 55.7963, 49.1088), "новосибирск": Place("Новосибирск", "в Новосибирске", 55.0302, 82.9204),
         "екатеринбург": Place("Екатеринбург", "в Екатеринбурге", 56.8389, 60.6057), "долгопрудный": DOLGOPRUDNY}


def temp(t):
    t = round(t)
    return "+%d" % t if t > 0 else "%d" % t  # "-5" is said "минус пять", "0" "ноль"


def describe(code):
    return CODES.get(int(code), "без особых явлений")


def day_name(d, today):
    near = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get((d - today).days)
    return near or WEEKDAYS_ACC[d.weekday()]


def part_of_day(hours):
    """Hours of the day (0-23) -> "утром", "днём и вечером"..."""
    parts = []
    for name, lo, hi in (("ночью", 0, 6), ("утром", 6, 12), ("днём", 12, 18), ("вечером", 18, 24)):
        if any(lo <= h < hi for h in hours):
            parts.append(name)
    return " и ".join(parts[:2]) if len(parts) <= 2 else "весь день"


class Weather:
    def __init__(self, web, home=MOSCOW, clock=time.monotonic, now=datetime.now, home_name="Москва", store=None,
                 wall=time.time):
        self.web = web
        self._home = home
        self.home_name = home_name
        self.clock = clock
        self.now = now
        self.wall = wall
        self.store = store  # the home forecast on disk: {"place": [...], "fetched": wall time, "data": ...}
        self._cache = {}  # (lat, lon) -> (fetched at, forecast)
        self._places = {}  # what was said -> Place
        self._lock = threading.Lock()
        self._home_data = None  # (wall time fetched, forecast) of the home city
        self._upgraded = False

    @property
    def home(self):
        """The city of "какая погода?" - found by its name the first time, when it is not Moscow."""
        if self._home is None:
            self._home = self.place("в " + self.home_name) or MOSCOW
        return self._home

    # --- data

    def start(self):
        """Collect the home forecast in the background: now if there is none on disk (or it is older than
        6 hours), then every 6 hours; after a failure, again in 10 minutes."""
        def loop():
            while True:
                try:
                    wait = self.refresh_home()
                except Offline:
                    wait = 600
                time.sleep(wait)
        threading.Thread(target=loop, daemon=True, name="weather").start()

    def refresh_home(self):
        """Fetch the home forecast if it is missing or 6 hours old; -> seconds until it is due again."""
        self._load()
        home = self.home
        age = self.wall() - self._home_data[0] if self._home_data else None
        if age is not None and age < HOME_REFRESH:
            return HOME_REFRESH - age
        data = self._fetch(home)
        with self._lock:
            self._home_data = (self.wall(), data)
        self._save(home)
        return HOME_REFRESH

    def _load(self):
        if self._home_data is not None or not self.store:
            return
        try:
            saved = json.loads(open(self.store, encoding="utf-8").read())
        except (OSError, ValueError):
            return
        home = self.home
        if saved.get("place") == [home.name, home.lat, home.lon]:  # the home city may have been changed
            self._home_data = (saved["fetched"], saved["data"])

    def _save(self, home):
        if not self.store:
            return
        tmp = str(self.store) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"place": [home.name, home.lat, home.lon], "fetched": self._home_data[0],
                       "data": self._home_data[1]}, f, ensure_ascii=False)
        os.replace(tmp, self.store)

    def _fetch(self, place):
        return self.web.get_json(FORECAST, {
            "latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": DAYS,
            "wind_speed_unit": "ms",
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
                     "precipitation_sum,wind_speed_10m_max,sunrise,sunset",
            "hourly": "temperature_2m,apparent_temperature,precipitation_probability,weather_code,wind_speed_10m,"
                      "relative_humidity_2m",
        })

    def forecast(self, place, fresh=False):
        if place == self.home:
            self._load()
            if self._home_data is None or fresh:
                self.refresh_home() if not fresh else self._refresh_now(place)
            elif "sunset" not in (self._home_data[1].get("daily") or {}) and not self._upgraded:
                self._upgraded = True  # kept on disk before sunsets and humidity were collected: once more, now
                try:
                    self._refresh_now(place)
                except Offline:
                    pass
            return self._home_data[1]
        key = (place.lat, place.lon)
        with self._lock:
            cached = self._cache.get(key)
        if cached and not fresh and self.clock() - cached[0] < REFRESH + 60:
            return cached[1]
        try:
            data = self._fetch(place)
        except Offline:
            if cached and self.clock() - cached[0] < 6 * 3600:  # stale, but better than nothing
                return cached[1]
            raise
        with self._lock:
            self._cache[key] = (self.clock(), data)
        return data

    def place(self, said):
        """"в Сочи", "в Нижнем Новгороде", "в Питере" -> Place, or None when there is no such city."""
        text = re.sub(r"^(?:в|во|на|для|у)\s+", "", said.strip(), flags=re.I).strip(" ?.!,")
        if not text:
            return self.home
        key = text.lower().replace("ё", "е")
        if key in self._places:
            return self._places[key]
        if key == "мск":
            key = "москва"
        for place in KNOWN.values():  # "в Москве", "Казань": no geocoder for the known ones
            if key in (place.name.lower().replace("ё", "е"), place.where.split(" ", 1)[1].lower().replace("ё", "е")):
                return self.home if self.home == place else place
        # "в Питере": the short names, whatever their ending
        name = next((v for k, v in ALIASES.items() if key == k or (len(k) > 3 and key.startswith(k[:-1]))), None)
        found = self._geocode(name) if name else self._geocode(text)
        if found and not name and found[0].get("country_code") not in (None, "RU") \
                and found[0]["name"].lower().replace("ё", "е") != key:  # "Токио" is Tokyo; "Твери" is not "Тверия"
            # "в Твери" found Tiberias in Israel (+25 for Tver): a Russian town of that stem comes first, if there is one
            home_town = self._by_stems(text, country="RU")
            if home_town:
                found = home_town
        if not found:
            # "в Казани", "в Нижнем Новгороде": the geocoder finds names by their start, so the ending is
            # cut off letter by letter ("Казан", then "Каза"), and the other words must fit too
            words = [w.lower().replace("ё", "е") for w in re.split(r"[\s-]+", text) if w]
            for cut in (1, 2):
                stems = [w[:-cut] if len(w) > cut + 2 else w for w in words]
                for r in self._geocode(stems[0], count=10):
                    if not str(r.get("feature_code", "PPL")).startswith("PPL"):
                        continue  # a town, not "Казахстан"
                    parts = [p.lower().replace("ё", "е") for p in re.split(r"[\s-]+", r["name"])]
                    if len(parts) >= len(stems) and all(p.startswith(s) for p, s in zip(parts, stems)):
                        found = [r]
                        break
                if found:
                    break
        if not found:
            return None
        r = found[0]
        said = " ".join(w[:1].upper() + w[1:] if w.islower() else w for w in text.split())  # "в питере" -> "в Питере"
        place = Place(r["name"], "в " + said, r["latitude"], r["longitude"], r.get("timezone") or "Europe/Moscow")
        self._places[key] = place
        return place

    def _by_stems(self, text, country=None):
        """The town whose words start as the said ones, less their endings ("Твери" -> "Твер" -> Тверь)."""
        words = [w.lower().replace("ё", "е") for w in re.split(r"[\s-]+", text) if w]
        for cut in (1, 2):
            stems = [w[:-cut] if len(w) > cut + 2 else w for w in words]
            for r in self._geocode(stems[0], count=10):
                if not str(r.get("feature_code", "PPL")).startswith("PPL") or country and r.get("country_code") != country:
                    continue
                if country and (r.get("population") or 0) < 5000:
                    continue  # a village of that stem is no reason to leave the town abroad
                parts = [p.lower().replace("ё", "е") for p in re.split(r"[\s-]+", r["name"])]
                if len(parts) >= len(stems) and all(p.startswith(s) for p, s in zip(parts, stems)):
                    return [r]
        return None

    def _geocode(self, name, count=5):
        data = self.web.get_json(GEOCODE, {"name": name, "count": count, "language": "ru"})
        results = data.get("results") or []
        return sorted(results, key=lambda r: -(r.get("population") or 0)) if count > 1 else results

    # --- answers

    def _refresh_now(self, place):
        data = self._fetch(place)
        with self._lock:
            self._home_data = (self.wall(), data)
        self._save(place)

    def current(self, place):
        """The weather now: the hour it is, from the forecast (the home one may be hours old),
        or what the service said was current when the forecast is fresh."""
        data = self.forecast(place)
        hourly = data.get("hourly") or {}
        stamp = self.now().strftime("%Y-%m-%dT%H:00")
        if "temperature_2m" in hourly and stamp in hourly.get("time", []):
            i = hourly["time"].index(stamp)
            return {k: hourly[k][i] for k in ("temperature_2m", "apparent_temperature", "weather_code", "wind_speed_10m")}
        return data["current"]

    def hours_text(self, place, d, first_hour, last_hour, part, named=None):
        """"Какая погода вечером?" -> from the hourly forecast of that part of the day. [named]: the day it
        is said of - the night after today is "сегодня ночью", though its hours are tomorrow's."""
        data = self.forecast(place)
        hourly = data.get("hourly") or {}
        rows = [i for i, t in enumerate(hourly.get("time", []))
                if t.startswith(d.isoformat()) and first_hour <= int(t[11:13]) < last_hour]
        if not rows or "temperature_2m" not in hourly:
            return None
        temps = [hourly["temperature_2m"][i] for i in rows]
        codes = [hourly["weather_code"][i] for i in rows]
        rain = max(hourly["precipitation_probability"][i] or 0 for i in rows)
        worst = max(codes, key=lambda c: (c in RAIN or c in SNOW, c))  # rain and snow matter more than clouds
        lo, hi = temp(min(temps)), temp(max(temps))
        text = "%s %s %s %s, %s" % (day_name(named or d, self.now().date()).capitalize(), part, place.where,
                                    lo if lo == hi else "от %s до %s" % (lo, hi), describe(worst))
        if rain >= 20:
            text += ", вероятность осадков %d%%" % round(rain)
        return text + "."

    def now_text(self, place):
        cur = self.current(place)
        t, feel = cur["temperature_2m"], cur["apparent_temperature"]
        text = "Сейчас %s %s, %s" % (place.where, temp(t), describe(cur["weather_code"]))
        if abs(round(feel) - round(t)) >= 2:
            text += ", ощущается как %s" % temp(feel)
        wind = round(cur["wind_speed_10m"])
        text += ", ветер %d м/с" % wind if wind >= 1 else ", безветренно"
        return text + "."

    def degrees(self, place):
        cur = self.current(place)
        return "Сейчас %s %s." % (place.where, temp(cur["temperature_2m"]))

    def _day(self, data, d):
        daily = data["daily"]
        try:
            i = daily["time"].index(d.isoformat())
        except ValueError:
            return None
        return {k: v[i] for k, v in daily.items()}

    def day_text(self, place, d, with_place=True):
        data = self.forecast(place)
        today = self.now().date()
        day = self._day(data, d)
        if day is None:
            return "Прогноз есть только на неделю вперёд."
        text = "%s%s от %s до %s, %s" % (day_name(d, today).capitalize(), " " + place.where if with_place else "",
                                        temp(day["temperature_2m_min"]), temp(day["temperature_2m_max"]),
                                        describe(day["weather_code"]))
        chance = day.get("precipitation_probability_max") or 0
        if chance >= 20:
            text += ", вероятность осадков %d%%" % round(chance)
        if d == today:
            text += "; сейчас %s" % temp(self.current(place)["temperature_2m"])
        return text + "."

    def days_text(self, place, first, last):
        today = self.now().date()
        last = min(last, today + timedelta(days=DAYS - 1))
        days = []
        d = first
        while d <= last:
            days.append(d)
            d += timedelta(days=1)
        if not days:
            return "Прогноз есть только на неделю вперёд."
        parts = [self.day_text(place, d, with_place=False).rstrip(".") for d in days]
        parts[0] = parts[0][:1].lower() + parts[0][1:]
        return "%s: %s." % (place.where[:1].upper() + place.where[1:], ". ".join(parts))

    def precipitation_days(self, place, first, last, snow=False):
        """"будет ли дождь на выходных?" -> each day of the span, one answer."""
        if first == last:
            return self.precipitation(place, first, snow)
        answers = []
        d = first
        while d <= last:
            answers.append(self.precipitation(place, d, snow))
            d += timedelta(days=1)
        yes = [a for a in answers if not a.startswith("Нет")]
        if not yes:
            return "Нет, %s не ожидается." % ("снега" if snow else "дождя")
        return " ".join(yes)

    def precipitation(self, place, d, snow=False):
        """"будет ли дождь завтра?" -> yes/no with the chance and the part of the day."""
        data = self.forecast(place)
        today = self.now().date()
        day = self._day(data, d)
        if day is None:
            return "Прогноз есть только на неделю вперёд."
        when = day_name(d, today)
        hourly = data.get("hourly") or {}
        hours = [int(t[11:13]) for t, p, c in zip(hourly.get("time", []), hourly.get("precipitation_probability", []),
                                                    hourly.get("weather_code", []))
                 if t.startswith(d.isoformat()) and (p or 0) >= 50 and (c in (SNOW if snow else RAIN))
                 and (d != today or int(t[11:13]) >= self.now().hour)]
        chance = round(day.get("precipitation_probability_max") or 0)
        kind = "снег" if snow else "дождь"
        codes = SNOW if snow else RAIN
        where = "" if place == self.home else " " + place.where
        if snow and (day.get("temperature_2m_min") or 0) > 3:
            return "Нет, %s%s снега не будет: слишком тепло." % (when, where)  # "+18 в Токио": "возможен снег" was the rain's
        if hours or (day["weather_code"] in codes and chance >= 40):
            return "Да, %s%s ожидается %s: вероятность %d%%%s." % (when, where, kind, chance,
                                                                  ", " + part_of_day(hours) if hours else "")
        if chance >= 30:
            return "Возможен %s: %s%s вероятность осадков %d%%." % ("снег" if snow else "дождь", when, where, chance)
        return "Нет, %s%s %s не ожидается." % (when, where, "снега" if snow else "дождя")

    def thunder(self, place, first, last):
        """"Будет ли гроза?" -> the hours of the days with a thunderstorm in the forecast."""
        data = self.forecast(place)
        today = self.now().date()
        hourly = data.get("hourly") or {}
        where = "" if place == self.home else " " + place.where
        found = []
        d = first
        while d <= last:
            hours = [int(t[11:13]) for t, c in zip(hourly.get("time", []), hourly.get("weather_code", []))
                     if t.startswith(d.isoformat()) and c in THUNDER and (d != today or int(t[11:13]) >= self.now().hour)]
            if hours:
                found.append("%s %s" % (day_name(d, today), part_of_day(hours)))
            d += timedelta(days=1)
        if not found:
            return "Нет, %s%s грозы не ожидается." % (day_name(first, today) if first == last else "в эти дни", where)
        return "Да, возможна гроза%s: %s." % (where, "; ".join(found))

    def wind_text(self, place, d=None):
        today = self.now().date()
        if d is None:  # no day named: now
            wind = round(self.current(place)["wind_speed_10m"])
            return "Сейчас %s ветер %d м/с." % (place.where, wind) if wind >= 1 else "Сейчас %s безветренно." % place.where
        day = self._day(self.forecast(place), d)
        if day is None:
            return "Прогноз есть только на неделю вперёд."
        return "%s %s ветер до %d м/с." % (day_name(d, today).capitalize(), place.where, round(day["wind_speed_10m_max"]))

    def humidity_text(self, place, d=None):
        data = self.forecast(place)
        today = self.now().date()
        hourly = data.get("hourly") or {}
        if "relative_humidity_2m" not in hourly:
            return "Влажность я пока не знаю: в прогнозе её нет."
        if d is None or d == today:
            cur = data.get("current") or {}
            stamp = self.now().strftime("%Y-%m-%dT%H:00")
            times = hourly.get("time", [])
            value = hourly["relative_humidity_2m"][times.index(stamp)] if stamp in times else cur.get("relative_humidity_2m")
            return "Сейчас %s влажность %d%%." % (place.where, round(value))
        values = [v for t, v in zip(hourly.get("time", []), hourly["relative_humidity_2m"])
                  if t.startswith(d.isoformat()) and 9 <= int(t[11:13]) < 21]
        if not values:
            return "Прогноз есть только на неделю вперёд."
        return "%s %s днём влажность от %d до %d%%." % (day_name(d, today).capitalize(), place.where, min(values), max(values))

    def sun_text(self, place, d, which=None):
        """Sunrise and sunset; [which]: "sunrise" or "sunset" alone ("во сколько закат?")."""
        day = self._day(self.forecast(place), d)
        if day is None or not day.get("sunset"):
            return "Этого в прогнозе нет."
        today = self.now().date()
        said = {"sunrise": "восход в %s" % day["sunrise"][11:16], "sunset": "закат в %s" % day["sunset"][11:16]}
        return "%s %s %s." % (day_name(d, today).capitalize(), place.where,
                              said[which] if which in said else "%s, %s" % (said["sunrise"], said["sunset"]))

    def clothes(self, place, d, hours=None, named=None):
        """What to wear on [d]; [hours]: (from, to, "утром") - by that part of the day ("завтра утром": its
        coldest hour, not the day's warmest); [named]: the day it is said of (as in hours_text)."""
        data = self.forecast(place)
        today = self.now().date()
        hourly = data.get("hourly") or {}
        rows = []
        if hours and "apparent_temperature" in hourly:
            rows = [i for i, t in enumerate(hourly.get("time", []))
                    if t.startswith(d.isoformat()) and hours[0] <= int(t[11:13]) < hours[1]]
        if rows:
            feel = min(hourly["apparent_temperature"][i] for i in rows)
        elif d == today:
            feel = self.current(place)["apparent_temperature"]
        else:
            day = self._day(data, d)
            if day is None:
                return "Прогноз есть только на неделю вперёд."
            feel = day["temperature_2m_max"]
        advice = ("очень холодно, нужна тёплая зимняя одежда" if feel < -10 else
                  "зимняя куртка, шапка и перчатки" if feel < 0 else
                  "тёплая куртка" if feel < 10 else
                  "лёгкая куртка или свитер" if feel < 18 else
                  "можно в лёгкой одежде" if feel < 25 else "жарко, одевайтесь легко")
        day = self._day(data, d) or {}
        umbrella = (day.get("precipitation_probability_max") or 0) >= 50 and day.get("weather_code") in RAIN
        if rows:
            umbrella = max(hourly["precipitation_probability"][i] or 0 for i in rows) >= 50
        when = day_name(named or d, today) + (" " + hours[2] if rows else "")
        return "%s %s, ощущается как %s: %s%s." % (when.capitalize(), place.where, temp(feel), advice,
                                                  ", и возьми зонт" if umbrella else "")


