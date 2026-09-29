#!/usr/bin/env python3
"""Download language models for Ollama without Ollama installed.

From the Ollama registry, in Ollama's own layout, so copying the result into Ollama's
models directory (for the system service: ~ollama/.ollama/models) makes the model available:
    fetch_ollama_model.py huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M DEST
    -> DEST/manifests/registry.ollama.ai/<namespace>/<name>/<tag>, DEST/blobs/sha256-<hex>

A GGUF file from Hugging Face, with the chat template (tool calls included) of an Ollama
library model of the same family; on the target, `ollama create NAME -f Modelfile` in that dir:
    fetch_ollama_model.py --gguf mradermacher/Qwen2.5-3B-Instruct-abliterated-GGUF \\
        Qwen2.5-3B-Instruct-abliterated.Q4_K_M.gguf qwen2.5:3b qwen2.5-3b-abliterated DEST
    -> DEST/gguf/<name>/<file>, DEST/gguf/<name>/Modelfile

Every file is checked against its sha256; files already present and valid are kept,
and interrupted downloads resume.
"""

import hashlib
import json
import os
import sys
import time
import urllib.request

REGISTRY = "registry.ollama.ai"
ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"


def split_name(model):
    name, _, tag = model.partition(":")
    namespace, _, repo = name.rpartition("/")
    return namespace or "library", repo, tag or "latest"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(url, path, size, hexdigest):
    """url -> path, resuming after dropped connections, verified by sha256."""
    if os.path.exists(path) and os.path.getsize(path) == size and sha256_of(path) == hexdigest:
        return
    tmp = path + ".part"
    label = os.path.basename(path)[:24]
    for attempt in range(30):  # the connection may drop; each attempt resumes where the file ends
        done = os.path.getsize(tmp) if os.path.exists(tmp) else 0
        if done >= size:
            break
        req = urllib.request.Request(url, headers={"Range": "bytes=%d-" % done} if done else {})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(tmp, "ab" if resp.status == 206 else "wb") as out:
                got = done if resp.status == 206 else 0
                while True:
                    block = resp.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
                    got += len(block)
                    if size > 50 << 20:
                        print("\r  %s: %d / %d МБ" % (label, got >> 20, size >> 20), end="", flush=True)
        except OSError as exc:
            print("\n  обрыв (%s), продолжаю" % exc, flush=True)
            time.sleep(min(2 ** attempt, 30))
    if size > 50 << 20:
        print()
    if sha256_of(tmp) != hexdigest:
        os.remove(tmp)
        sys.exit("контрольная сумма не сошлась: %s" % path)
    os.replace(tmp, path)


def get_json(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60) as resp:
        return resp.read()


def registry_manifest(model):
    namespace, repo, tag = split_name(model)
    raw = get_json("https://%s/v2/%s/%s/manifests/%s" % (REGISTRY, namespace, repo, tag), {"Accept": ACCEPT})
    return namespace, repo, tag, raw, json.loads(raw)


def blob_url(namespace, repo, digest):
    return "https://%s/v2/%s/%s/blobs/%s" % (REGISTRY, namespace, repo, digest)


def fetch_registry_model(model, dest):
    namespace, repo, tag, raw, manifest = registry_manifest(model)
    os.makedirs(os.path.join(dest, "blobs"), exist_ok=True)
    layers = [manifest["config"]] + manifest["layers"]
    for layer in layers:
        hexdigest = layer["digest"].split(":", 1)[1]
        download(blob_url(namespace, repo, layer["digest"]),
                 os.path.join(dest, "blobs", "sha256-" + hexdigest), layer["size"], hexdigest)
    mdir = os.path.join(dest, "manifests", REGISTRY, namespace, repo)
    os.makedirs(mdir, exist_ok=True)
    with open(os.path.join(mdir, tag), "wb") as f:
        f.write(raw)
    print("модель %s готова (%d МБ)" % (model, sum(layer["size"] for layer in layers) >> 20))


def fetch_gguf_model(hf_repo, filename, template_from, name, dest):
    tree = json.loads(get_json("https://huggingface.co/api/models/%s/tree/main" % hf_repo))
    entry = next((f for f in tree if f["path"] == filename), None)
    if not entry:
        sys.exit("в %s нет файла %s" % (hf_repo, filename))
    mdir = os.path.join(dest, "gguf", name)
    os.makedirs(mdir, exist_ok=True)
    download("https://huggingface.co/%s/resolve/main/%s" % (hf_repo, filename),
             os.path.join(mdir, filename), entry["lfs"]["size"], entry["lfs"]["oid"])

    namespace, repo, _, _, manifest = registry_manifest(template_from)
    lines = ["# %s from https://huggingface.co/%s" % (filename, hf_repo),
             "# chat template (with tool calls) from the Ollama library model %s" % template_from,
             "FROM ./%s" % filename]
    for layer in manifest["layers"]:
        kind = layer["mediaType"].rsplit(".", 1)[-1]
        if kind == "template":
            template = get_json(blob_url(namespace, repo, layer["digest"])).decode()
            lines.append('TEMPLATE """%s"""' % template)
        elif kind == "params":
            for key, value in json.loads(get_json(blob_url(namespace, repo, layer["digest"]))).items():
                for v in value if isinstance(value, list) else [value]:
                    lines.append("PARAMETER %s %s" % (key, json.dumps(v) if isinstance(v, str) else v))
    with open(os.path.join(mdir, "Modelfile"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("модель %s готова (%d МБ)" % (name, entry["lfs"]["size"] >> 20))


def main():
    if sys.argv[1] == "--gguf":
        fetch_gguf_model(*sys.argv[2:7])
    else:
        fetch_registry_model(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
