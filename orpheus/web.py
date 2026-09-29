"""The only way out to the internet: weather, search and the Central Bank's exchange rates.

Through the proxy in the config (ORPHEUS_PROXY: sing-box on this laptop, to the exit node) and only
through it - never around it: without the proxy the internet is "unavailable" rather than reached
another way. Only for these requests: Ollama, the Planner and the phone never see a proxy.

Only the hosts below, and only the requests this program builds itself: nothing a phrase or a
search result says becomes a URL to open (the proxy has no password; any local process could use
a service that fetched arbitrary URLs).
"""

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

HOSTS = {"api.open-meteo.com", "geocoding-api.open-meteo.com", "ru.wikipedia.org", "html.duckduckgo.com", "www.cbr.ru"}
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
# Wikimedia asks programs to name themselves (a browser's name gets "429 Too Many Requests" sooner)
AGENTS = {"ru.wikipedia.org": "Orpheus/0.1 (personal voice assistant; https://github.com/arsenii-cmd/Orpheus) python-urllib"}
TIMEOUT = 8
# now and then a request hangs through the proxy and the same one a second later takes 0.5 s (27.09:
# Wikipedia and the Central Bank, 12 s each): a short first try, then one more
FIRST_TRY = 4


class Offline(Exception):
    """The service could not be reached (no proxy, no network, an error from it)."""


class Web:
    def __init__(self, proxy="", timeout=TIMEOUT, searxng=""):
        self.proxy = proxy
        self.timeout = timeout
        # ProxyHandler({}) when there is no proxy: never the HTTP(S)_PROXY of the environment
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
        # SearXNG on this laptop (127.0.0.1): asked directly, it goes out through the proxy itself
        self.searxng = searxng.rstrip("/")
        if self.searxng and urlsplit(self.searxng).hostname not in ("127.0.0.1", "localhost"):
            raise ValueError("SearXNG — только на этом ноуте: %s" % self.searxng)
        self.local = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, url, params=None, headers=None):
        host = urlsplit(url).hostname
        local = bool(self.searxng) and url.startswith(self.searxng + "/")
        if not local and (host not in HOSTS or urlsplit(url).scheme != "https"):
            raise ValueError("адрес не из разрешённых: %s" % host)
        if params:
            url += "?" + urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": AGENTS.get(host, USER_AGENT), "Accept-Language": "ru-RU,ru;q=0.9",
                                                   **(headers or {})})
        tries = (min(FIRST_TRY, self.timeout), self.timeout)
        for i, timeout in enumerate(tries):
            try:
                with (self.local if local else self.opener).open(req, timeout=timeout) as r:
                    return r.read().decode("utf-8", "replace")
            except (urllib.error.URLError, OSError, ValueError) as exc:
                reason = exc.reason if isinstance(exc, urllib.error.URLError) and not isinstance(exc, urllib.error.HTTPError) else exc
                if i + 1 < len(tries) and isinstance(reason, (socket.timeout, TimeoutError, ConnectionResetError)):
                    continue  # hung: once more (an answer from the service - an error, a captcha - is not tried again)
                raise Offline(str(exc)) from None

    def get_json(self, url, params=None):
        text = self.get(url, params, {"Accept": "application/json"})
        try:
            return json.loads(text)
        except ValueError:
            raise Offline("ответ не JSON") from None


def online(config):
    """(weather, search) for the config, or (None, None) with the internet turned off ("-")."""
    if config.proxy == "-":
        return None, None
    from .search import Search
    from .weather import KNOWN, Weather

    web = Web(config.proxy, searxng=config.searxng)
    weather = Weather(web, home=KNOWN.get(config.city.strip().lower() or "москва"), home_name=config.city,
                      store=config.db.with_name("weather.json"))
    return weather, Search(web)
