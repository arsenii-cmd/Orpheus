import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("fetch_debs", Path(__file__).parent.parent / "usb/fetch_debs.py")
fetch_debs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_debs)


def test_debian_version_order():
    c = fetch_debs.compare
    assert c("1.0", "1.0") == 0
    assert c("1.0~rc1", "1.0") < 0
    assert c("1:0.1", "2.0") > 0
    assert c("3.12.3-1ubuntu0.17", "3.12.3-1ubuntu0.9") > 0
    assert c("1.2.11-1ubuntu0.3", "1.2.11-1build2") > 0
    assert c("2.0a", "2.0") > 0 and c("2.0+b1", "2.0a") > 0


def test_relations():
    assert fetch_debs.parse_relations("a (>= 1.0), b:any | c, d [amd64]") == [
        [("a", ">=", "1.0")], [("b", None, None), ("c", None, None)], [("d", None, None)]]


def pkg(name, version, depends="", source=None, provides=""):
    s = {"Package": name, "Version": version, "Architecture": "amd64", "Filename": "pool/%s.deb" % name}
    if depends:
        s["Depends"] = depends
    if source:
        s["Source"] = source
    if provides:
        s["Provides"] = provides
    return s


def test_resolver_takes_missing_and_upgrades_with_siblings():
    archive = fetch_debs.Archive([
        pkg("py-venv", "2", "py (= 2), pip-whl"),
        pkg("py", "2", "libpy (= 2)", source="py"),
        pkg("libpy", "2", source="py"),
        pkg("libpy-extra", "2", "libpy (= 2)", source="py"),
        pkg("pip-whl", "1"),
        pkg("sound", "1", "libasound | libasound-virtual"),
        pkg("alsa-real", "1", provides="libasound-virtual"),
    ])
    installed = {"py": "1", "libpy": "1", "libpy-extra": "1", "alsa-real": "1"}
    r = fetch_debs.Resolver(archive, installed)
    assert r.want("py-venv") and r.want("sound")
    assert r.problems == []
    assert sorted(r.chosen) == ["libpy", "libpy-extra", "pip-whl", "py", "py-venv", "sound"]  # not alsa-real: provided


def test_resolver_takes_recommends_but_does_not_require_them():
    a = pkg("alsa-utils", "1")
    a["Recommends"] = "alsa-ucm-conf, ghost"
    r = fetch_debs.Resolver(fetch_debs.Archive([a, pkg("alsa-ucm-conf", "1")]), {})
    assert r.want("alsa-utils")
    assert r.problems == [] and sorted(r.chosen) == ["alsa-ucm-conf", "alsa-utils"]


def test_resolver_reports_what_it_cannot_find():
    r = fetch_debs.Resolver(fetch_debs.Archive([pkg("a", "1", "ghost (>= 2)")]), {})
    assert r.want("a")
    assert r.problems and "ghost" in r.problems[0]


def test_manifests_apply_diffs(tmp_path):
    base = tmp_path / "base"
    base.write_text("a\t1\nb:amd64\t2\n")
    diff = tmp_path / "diff"
    diff.write_text("--- x\n+++ y\n+c\t3\n-a\t1\n")
    assert fetch_debs.read_manifests([base, diff]) == {"b": "2", "c": "3"}
