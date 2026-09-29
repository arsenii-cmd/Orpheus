#!/usr/bin/env python3
"""Download the .deb files Orpheus needs on a fresh Ubuntu 24.04, so the first boot works offline.

    fetch_debs.py --installed base.manifest --installed diff.manifest --out DIR [--cache DIR] pkg ...

--installed: package lists from the ISO (casper/*.manifest): what the fresh system already has.
A diff manifest ("+pkg ver" / "-pkg ver" lines) is applied on top of the previous one.
Only what is missing, or too old for a dependency, is downloaded. Recommended packages are taken
too when the archive has them (apt would install them online), but they never fail the resolution. When an installed package has to
be upgraded, its installed siblings from the same source package are upgraded with it, since they
usually depend on each other with an exact version.
Needs only the Python standard library, so it runs on any Linux build machine.
"""

import argparse
import gzip
import hashlib
import os
import re
import sys
import urllib.request
from pathlib import Path

MIRROR = os.environ.get("ORPHEUS_MIRROR", "http://archive.ubuntu.com/ubuntu").rstrip("/")
SUITES = ["noble", "noble-updates", "noble-security"]
COMPONENTS = ["main", "universe"]
ARCHES = ("amd64", "all")


# ---- Debian version comparison (Debian Policy 5.6.12)

def _order(c):
    if c == "~":
        return -1
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    return ord(c) + 256


def _compare_part(a, b):
    while a or b:
        na = re.match(r"^[^\d]*", a).group(0)
        nb = re.match(r"^[^\d]*", b).group(0)
        for i in range(max(len(na), len(nb))):
            ca = _order(na[i]) if i < len(na) else 0
            cb = _order(nb[i]) if i < len(nb) else 0
            if ca != cb:
                return -1 if ca < cb else 1
        a, b = a[len(na):], b[len(nb):]
        da = re.match(r"^\d*", a).group(0)
        db = re.match(r"^\d*", b).group(0)
        if int(da or 0) != int(db or 0):
            return -1 if int(da or 0) < int(db or 0) else 1
        a, b = a[len(da):], b[len(db):]
    return 0


def _split(v):
    epoch, _, rest = v.rpartition(":") if ":" in v else ("0", "", v)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "0")
    return int(epoch or 0), upstream, revision


def compare(a, b):
    ea, ua, ra = _split(a)
    eb, ub, rb = _split(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _compare_part(ua, ub) or _compare_part(ra, rb)


def satisfies(version, op, wanted):
    if op is None:
        return True
    c = compare(version, wanted)
    return {"<<": c < 0, "<=": c <= 0, "=": c == 0, ">=": c >= 0, ">>": c > 0,
            "<": c <= 0, ">": c >= 0}[op]


# ---- indexes

def parse_relations(text):
    """'a (>= 1) | b:any, c' -> [[("a", ">=", "1"), ("b", None, None)], [("c", None, None)]]"""
    groups = []
    for group in filter(None, (g.strip() for g in (text or "").split(","))):
        alts = []
        for alt in group.split("|"):
            m = re.match(r"\s*([^\s:(\[]+)(?::\S+)?\s*(?:\(\s*([<>=]+)\s*([^)\s]+)\s*\))?", alt)
            if m:
                alts.append((m.group(1), m.group(2), m.group(3)))
        if alts:
            groups.append(alts)
    return groups


def parse_packages(text):
    for stanza in text.split("\n\n"):
        fields, key = {}, None
        for line in stanza.splitlines():
            if line.startswith((" ", "\t")) and key:
                fields[key] += "\n" + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        if "Package" in fields and fields.get("Architecture") in ARCHES:
            yield fields


class Archive:
    def __init__(self, stanzas):
        self.versions = {}   # name -> [stanza, ...]
        self.providers = {}  # virtual name -> [(real name, provided version or None)]
        for s in stanzas:
            self.versions.setdefault(s["Package"], []).append(s)
            for name, _, ver in (alts[0] for alts in parse_relations(s.get("Provides"))):
                self.providers.setdefault(name, []).append((s["Package"], ver))
        for lst in self.versions.values():
            lst.sort(key=_Key)
            lst.reverse()  # newest first

    @classmethod
    def download(cls, cache: Path):
        cache.mkdir(parents=True, exist_ok=True)
        stanzas = []
        for suite in SUITES:
            for comp in COMPONENTS:
                path = cache / ("%s_%s_Packages.gz" % (suite, comp))
                url = "%s/dists/%s/%s/binary-amd64/Packages.gz" % (MIRROR, suite, comp)
                _fetch(url, path, refresh=True)
                stanzas += parse_packages(gzip.decompress(path.read_bytes()).decode("utf-8", "replace"))
        return cls(stanzas)


class _Key:
    def __init__(self, s):
        self.v = s["Version"]

    def __lt__(self, other):
        return compare(self.v, other.v) < 0


def read_manifests(paths):
    installed = {}
    for path in paths:
        for line in Path(path).read_text().splitlines():
            if not line.strip() or line.startswith(("---", "+++", "@@")):
                continue
            sign = line[0] if line[0] in "+-" else ""
            parts = line[len(sign):].split()
            if len(parts) < 2:
                continue
            name = parts[0].split(":")[0]
            if sign == "-":
                installed.pop(name, None)
            else:
                installed[name] = parts[1]
    return installed


# ---- resolution

class Resolver:
    def __init__(self, archive: Archive, installed: dict):
        self.archive = archive
        self.installed = dict(installed)  # name -> version, updated as packages are chosen
        self.chosen = {}                  # name -> stanza to download
        self.problems = []

    def _installed_ok(self, name, op, ver):
        if name in self.installed and satisfies(self.installed[name], op, ver):
            return True
        for real, pver in self.archive.providers.get(name, []):
            if real in self.installed and (op is None or (pver and satisfies(pver, op, ver))):
                return True
        return False

    def _candidate(self, name, op, ver):
        for s in self.archive.versions.get(name, []):
            if satisfies(s["Version"], op, ver):
                return s
        if op is None:
            for real, _ in self.archive.providers.get(name, []):
                if self.archive.versions.get(real):
                    return self.archive.versions[real][0]
        return None

    def want(self, name, op=None, ver=None, why="requested"):
        if self._installed_ok(name, op, ver):
            return True
        s = self._candidate(name, op, ver)
        if s is None:
            return False
        self._take(s, why)
        return True

    def _take(self, s, why):
        name = s["Package"]
        if name in self.chosen:
            return
        upgrading = name in self.installed
        self.chosen[name] = s
        self.installed[name] = s["Version"]
        for field in ("Pre-Depends", "Depends"):
            for alts in parse_relations(s.get(field)):
                if any(self._installed_ok(*alt) for alt in alts):
                    continue
                if not any(self.want(*alt, why=name) for alt in alts):
                    self.problems.append("%s: no candidate for %s" % (name, " | ".join(a[0] for a in alts)))
        for alts in parse_relations(s.get("Recommends")):
            if not any(self._installed_ok(*alt) for alt in alts):
                any(self.want(*alt, why=name) for alt in alts)
        if upgrading:
            self._upgrade_siblings(s)

    def _upgrade_siblings(self, s):
        source = (s.get("Source") or s["Package"]).split()[0]
        for name, installed_ver in list(self.installed.items()):
            if name in self.chosen:
                continue
            new = next((c for c in self.archive.versions.get(name, [])
                        if (c.get("Source") or c["Package"]).split()[0] == source
                        and c["Version"] == s["Version"]), None)
            if new and compare(new["Version"], installed_ver) > 0:
                self._take(new, "sibling of " + s["Package"])


def _fetch(url, path: Path, sha256=None, refresh=False):
    if path.exists() and not refresh and (sha256 is None or _sha256(path) == sha256):
        return
    tmp = path.with_suffix(path.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)
    if sha256 and _sha256(tmp) != sha256:
        tmp.unlink()
        raise RuntimeError("checksum mismatch: " + url)
    tmp.replace(path)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--installed", action="append", default=[])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/orpheus-usb/apt")
    ap.add_argument("packages", nargs="+")
    args = ap.parse_args()

    archive = Archive.download(args.cache)
    resolver = Resolver(archive, read_manifests(args.installed))
    for name in args.packages:
        if not resolver.want(name):
            sys.exit("нет пакета %s в архиве" % name)
    if resolver.problems:
        sys.exit("не удалось разрешить зависимости:\n  " + "\n  ".join(resolver.problems))

    args.out.mkdir(parents=True, exist_ok=True)
    for old in args.out.glob("*.deb"):
        old.unlink()
    for name, s in sorted(resolver.chosen.items()):
        filename = s["Filename"]
        cached = args.cache / "debs" / os.path.basename(filename)
        cached.parent.mkdir(parents=True, exist_ok=True)
        _fetch("%s/%s" % (MIRROR, filename), cached, sha256=s.get("SHA256"))
        os.link(cached, args.out / cached.name) if cached.stat().st_dev == args.out.stat().st_dev \
            else (args.out / cached.name).write_bytes(cached.read_bytes())
        print("  %s %s" % (name, s["Version"]))
    print("%d пакетов" % len(resolver.chosen))


if __name__ == "__main__":
    main()
