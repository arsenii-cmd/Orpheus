#!/usr/bin/env bash
# Build an Ubuntu Server 24.04 USB image that installs itself together with Orpheus.
#   scripts/make_usb.sh              build orpheus-ubuntu-server.iso
#   scripts/make_usb.sh /dev/sdX     build it and write it to the USB stick
# Runs on Linux (or WSL); needs xorriso, curl, openssl, python3, git.
#
# No secrets go into the image: the installer still shows its usual screens for the network
# (Wi-Fi included), the user and password, and SSH. Everything else is automatic: the whole
# disk, copying Orpheus, and the first-boot setup.
# The speech models and two language models go into the image too (~4.6 GB), so the laptop
# does not download them; ORPHEUS_BUNDLE_MODELS=0 leaves them out. The default model is
# Qwen3-4B; Qwen2.5-3B (ollama name qwen2.5-3b-abliterated) comes along to compare with.
# So do the system packages, the Python wheels and Ollama itself (without CUDA, ~40 MB), so the
# first boot needs no internet at all; ORPHEUS_BUNDLE_PACKAGES=0 leaves them out (needs zstd, pip).
# Answers can be given in advance: ORPHEUS_TZ, ORPHEUS_GITHUB_KEYS, ORPHEUS_MODEL.
#
# ORPHEUS_FROM=user@host moves an Orpheus that works (its laptop, reached by ssh) to a new one: instead
# of the stock models the image carries what that laptop runs - its speech models and voices, its
# language model (the GGUF and Modelfile named in the server's settings), plannerd and sing-box, the
# user services with their settings - and a snapshot of its data (the memory, "Личное" still encrypted,
# the Planner's database). Its secrets come along too (the phone's token, the sing-box subscription,
# the Planner's cloud key): such a stick is to be kept like a password.
set -euo pipefail
cd "$(dirname "$0")/.."

DEVICE="${1:-}"
CACHE="${ORPHEUS_CACHE:-$HOME/.cache/orpheus-usb}"
OUT="${ORPHEUS_ISO:-$PWD/orpheus-ubuntu-server.iso}"
RELEASE=https://releases.ubuntu.com/24.04

BUNDLE_PACKAGES="${ORPHEUS_BUNDLE_PACKAGES:-1}"
OLLAMA_VERSION="${ORPHEUS_OLLAMA_VERSION:-v0.32.0}"
# what the first boot installs with apt; everything else Orpheus needs is in the base system
DEBS="python3.12-venv libportaudio2 alsa-utils zram-tools console-setup libvulkan1 mesa-vulkan-drivers"

TOOLS="xorriso curl python3 git sha256sum"
[ "$BUNDLE_PACKAGES" = 1 ] && TOOLS="$TOOLS zstd"
for tool in $TOOLS; do
  command -v "$tool" >/dev/null || { echo "нужен $tool (xorriso — пакет xorriso или libisoburn)"; exit 1; }
done
if [ "$BUNDLE_PACKAGES" = 1 ] && ! python3 -m pip --version >/dev/null 2>&1; then
  echo "нужен pip (пакет python3-pip)"; exit 1
fi

ask() {  # ask <var> <prompt> [default]
  local var="$1" prompt="$2" default="${3:-}" value
  if [ -n "${!var+set}" ]; then return; fi  # given in the environment, even if empty
  read -rp "$prompt${default:+ [$default]}: " value
  value="${value:-$default}"
  printf -v "$var" '%s' "$value"
  export "$var"
}

ask ORPHEUS_TZ "Часовой пояс" "Europe/Moscow"
[[ "$ORPHEUS_TZ" =~ ^[A-Za-z0-9/_+-]+$ ]] || { echo "часовой пояс вида Europe/Moscow"; exit 1; }
ask ORPHEUS_GITHUB_KEYS "GitHub-логин, чьи SSH-ключи подставить (пусто — не надо)" ""

# 1. The stock image, verified against Ubuntu's checksums.
mkdir -p "$CACHE"
SUMS="$(curl -fsS "$RELEASE/SHA256SUMS")"
ISO_NAME="$(grep -o 'ubuntu-24\.04[0-9.]*-live-server-amd64\.iso' <<<"$SUMS" | sort -V | tail -1)"
ISO="$CACHE/$ISO_NAME"
if ! (cd "$CACHE" && grep " \*$ISO_NAME\$" <<<"$SUMS" | sha256sum -c --status 2>/dev/null); then
  echo "== скачиваю $ISO_NAME (~3 ГБ)"
  curl -fL --retry 3 -C - -o "$ISO" "$RELEASE/$ISO_NAME"
  (cd "$CACHE" && grep " \*$ISO_NAME\$" <<<"$SUMS" | sha256sum -c) || { rm -f "$ISO"; exit 1; }
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/orpheus"

# 1b. The models, cached next to the image: downloads resume and are checked by checksum.
MODEL="${ORPHEUS_MODEL:-huihui_ai/qwen3-abliterated:4b-instruct-2507-q4_K_M}"
# it goes into the installer's command line (stage.sh) and on to the first boot: nothing but a model's name
[[ "$MODEL" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "ORPHEUS_MODEL — имя модели Ollama, вида huihui_ai/qwen3-abliterated:4b"; exit 1; }
MODEL_MAPS=()
FROM="${ORPHEUS_FROM:-}"
if [ -n "$FROM" ]; then
  command -v rsync >/dev/null || { echo "нужен rsync"; exit 1; }
  echo "== переношу орфея с $FROM"
  M="$CACHE/from"
  mkdir -p "$M/models"
  # the voices it speaks with and hears with; English and unused Russian ones stay behind
  rsync -a --delete --exclude 'vits-piper-jarvis-*' --exclude 'vits-piper-ru_RU-denis-*' --exclude 'vits-piper-ru_RU-dmitri-*' \
    "$FROM:.local/share/orpheus/models/" "$M/models/"
  MODEL="$(ssh "$FROM" 'cat ~/.config/systemd/user/orpheus-server.service.d/*.conf 2>/dev/null' \
           | sed -n 's/^Environment=ORPHEUS_MODEL=//p' | tail -1)"
  [ -n "$MODEL" ] || { echo "на $FROM не нашёл ORPHEUS_MODEL в настройках сервера"; exit 1; }
  [[ "$MODEL" =~ ^[A-Za-z0-9._:/-]+$ ]] || { echo "странное имя модели на $FROM: $MODEL"; exit 1; }
  GGUF="$(ssh "$FROM" "sed -n 's/^FROM //p' ~/models/$MODEL.Modelfile")"
  case "$GGUF" in /*|~*) ;; *) GGUF="models/$GGUF" ;; esac  # a relative FROM is next to the Modelfile, in ~/models
  echo "== языковая модель $MODEL"
  # one directory level per model: "huihui_ai/qwen3…" as "huihui_ai__qwen3…" (setup_ollama.sh turns "__" back into "/")
  MODEL_DIR="${MODEL//\//__}"
  rm -rf "$M/ollama" && mkdir -p "$M/ollama/gguf/$MODEL_DIR"
  mkdir -p "$M/gguf"
  rsync -a --partial "$FROM:$GGUF" "$M/gguf/"
  ln "$M/gguf/$(basename "$GGUF")" "$M/ollama/gguf/$MODEL_DIR/" 2>/dev/null || cp "$M/gguf/$(basename "$GGUF")" "$M/ollama/gguf/$MODEL_DIR/"
  ssh "$FROM" "cat ~/models/$MODEL.Modelfile" | sed 's#^FROM .*/#FROM ./#' > "$M/ollama/gguf/$MODEL_DIR/Modelfile"
  MODEL_MAPS=(-map "$M/models" /orpheus/models -map "$M/ollama" /orpheus/ollama)

  echo "== программы, службы, настройки, секреты и данные"
  # the databases copied by SQLite itself on the laptop: a consistent snapshot while they are in use
  ssh "$FROM" 'rm -rf /tmp/orpheus-move && mkdir -m 700 /tmp/orpheus-move && python3 - <<PY
import sqlite3, pathlib
home = pathlib.Path.home()
for src, dst in ((".local/share/orpheus/orpheus.db", "orpheus.db"), (".local/share/planner/planner.db", "planner.db")):
    if (home / src).exists():
        with sqlite3.connect(home / src) as a, sqlite3.connect("/tmp/orpheus-move/" + dst) as b:
            a.backup(b)
PY'
  H="$WORK/home"
  mkdir -p "$H/.local/share/orpheus" "$H/.local/share/planner" "$H/.config"
  rsync -a "$FROM:.local/bin" "$H/.local/" --exclude __pycache__
  # a laptop without the Planner or sing-box moves all the same
  for d in orpheus sing-box planner systemd; do rsync -a --ignore-missing-args "$FROM:.config/$d" "$H/.config/"; done
  rsync -a "$FROM:/tmp/orpheus-move/orpheus.db" "$H/.local/share/orpheus/"
  rsync -a --ignore-missing-args "$FROM:/tmp/orpheus-move/planner.db" "$H/.local/share/planner/"
  rsync -a --ignore-missing-args "$FROM:.local/share/orpheus/personal.db.enc" "$FROM:.local/share/orpheus/weather.json" \
    "$H/.local/share/orpheus/"
  ssh "$FROM" 'rm -rf /tmp/orpheus-move'
  ssh "$FROM" 'echo "$HOME"' > "$H/.orpheus-from-home"  # its paths in the unit files, replaced on the new laptop
  tar -C "$H" -cf "$WORK/orpheus/home.tar" .
  rm -rf "$H"
elif [ "${ORPHEUS_BUNDLE_MODELS:-1}" = 1 ]; then
  echo "== модели речи"
  ORPHEUS_MODELS="$CACHE/models" scripts/download_models.sh
  echo "== языковая модель $MODEL"
  python3 scripts/fetch_ollama_model.py "$MODEL" "$CACHE/ollama"
  echo "== вторая модель, Qwen2.5-3B"
  python3 scripts/fetch_ollama_model.py --gguf mradermacher/Qwen2.5-3B-Instruct-abliterated-GGUF \
    Qwen2.5-3B-Instruct-abliterated.Q4_K_M.gguf qwen2.5:3b qwen2.5-3b-abliterated "$CACHE/ollama"
  MODEL_MAPS=(-map "$CACHE/models" /orpheus/models -map "$CACHE/ollama" /orpheus/ollama)
fi

# 2. Orpheus as it is in this checkout, uncommitted changes included, ignored files (models, .venv) not.
python3 - "$WORK/orpheus/orpheus.tar" <<'PY'
import os, subprocess, sys, tarfile
files = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard"],
                       capture_output=True, check=True).stdout.decode().split("\0")
with tarfile.open(sys.argv[1], "w") as tar:
    for f in sorted(set(files)):
        if f and os.path.isfile(f):
            tar.add(f)
PY
cp usb/stage.sh "$WORK/orpheus/"

# 2b. System packages, Python wheels and Ollama, so the first boot needs no internet. Cached too.
PACKAGE_MAPS=()
if [ "$BUNDLE_PACKAGES" = 1 ]; then
  echo "== системные пакеты"
  mkdir -p "$WORK/manifests"
  # what a fresh install already has: only the rest is downloaded
  xorriso -osirrox on -indev "$ISO" \
    -extract /casper/ubuntu-server-minimal.manifest "$WORK/manifests/1" \
    -extract /casper/ubuntu-server-minimal.ubuntu-server.manifest "$WORK/manifests/2" >/dev/null 2>&1
  # shellcheck disable=SC2086
  python3 usb/fetch_debs.py --installed "$WORK/manifests/1" --installed "$WORK/manifests/2" \
    --out "$WORK/orpheus/offline/debs" --cache "$CACHE/apt" $DEBS | tail -1

  echo "== python-пакеты"
  mapfile -t REQS < <(python3 -c '
import re
block = re.search(r"dependencies = \[(.*?)\]", open("pyproject.toml").read(), re.S).group(1)
print("\n".join(re.findall(r"\"([^\"]+)\"", block)))')
  python3 -m pip download -q --only-binary=:all: --python-version 3.12 --implementation cp --abi cp312 \
    --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux2014_x86_64 \
    --platform any -d "$CACHE/wheels" "${REQS[@]}" "setuptools>=68" wheel

  echo "== ollama $OLLAMA_VERSION"
  TGZ="$CACHE/ollama-bin/ollama-$OLLAMA_VERSION-cpu.tgz"
  if [ ! -s "$TGZ" ]; then
    rm -rf "$CACHE/ollama-bin/unpack"
    mkdir -p "$CACHE/ollama-bin/unpack"
    curl -fL --retry 10 --retry-all-errors -C - -o "$CACHE/ollama-bin/ollama.tar.zst" \
      "https://github.com/ollama/ollama/releases/download/$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
    zstd -dc "$CACHE/ollama-bin/ollama.tar.zst" | tar -x -C "$CACHE/ollama-bin/unpack"
    # CUDA (~2 GB) is useless on the Vega iGPU; the CPU and Vulkan backends stay
    rm -rf "$CACHE/ollama-bin/unpack/lib/ollama/"cuda_* "$CACHE/ollama-bin/unpack/lib/ollama/"rocm*
    tar -czf "$TGZ.part" -C "$CACHE/ollama-bin/unpack" . && mv "$TGZ.part" "$TGZ"
    rm -rf "$CACHE/ollama-bin/unpack" "$CACHE/ollama-bin/ollama.tar.zst"
  fi
  PACKAGE_MAPS=(-map "$CACHE/wheels" /orpheus/offline/wheels -map "$TGZ" /orpheus/offline/ollama.tgz)
fi

# 3. The autoinstall answers (JSON is valid YAML).
KEYS=""
if [ -n "$ORPHEUS_GITHUB_KEYS" ]; then
  KEYS="$(curl -fsS "https://github.com/$ORPHEUS_GITHUB_KEYS.keys")" || { echo "не смог взять ключи $ORPHEUS_GITHUB_KEYS с GitHub"; exit 1; }
fi
export ORPHEUS_TZ KEYS MODEL
python3 - "$WORK" <<'PY'
import json, os, sys
env = os.environ
config = {"autoinstall": {
    "version": 1,
    # Screens the installer still shows: nothing secret is stored in the image.
    # network: cable or Wi-Fi with its password; identity: user, password, server name;
    # ssh: the prefilled values below are only defaults.
    "interactive-sections": ["network", "identity", "ssh"],
    "locale": "en_US.UTF-8",
    "keyboard": {"layout": "us"},
    "timezone": env["ORPHEUS_TZ"],
    "ssh": {"install-server": True, "allow-pw": True,
            "authorized-keys": [k for k in env["KEYS"].splitlines() if k.strip()]},
    # "direct": the whole disk, no LVM (the LVM layout would leave most of the disk unused)
    "storage": {"layout": {"name": "direct"}},
    "source": {"id": "ubuntu-server"},
    "drivers": {"install": False},
    "late-commands": [
        "mkdir -p /target/opt/orpheus-payload",
        "cp /cdrom/orpheus/orpheus.tar /cdrom/orpheus/stage.sh /target/opt/orpheus-payload/",
        "if [ -f /cdrom/orpheus/home.tar ]; then cp /cdrom/orpheus/home.tar /target/opt/orpheus-payload/; fi",
        "if [ -d /cdrom/orpheus/models ]; then cp -r /cdrom/orpheus/models /cdrom/orpheus/ollama /target/opt/orpheus-payload/; fi",
        "if [ -d /cdrom/orpheus/offline ]; then cp -r /cdrom/orpheus/offline /target/opt/orpheus-payload/; fi",
        # the model too: the first boot sets up the one the stick carries, not the default
        "curtin in-target --target=/target -- bash /opt/orpheus-payload/stage.sh %s %s" % (env["ORPHEUS_TZ"], env["MODEL"]),
    ],
    "shutdown": "reboot",
}}
with open(os.path.join(sys.argv[1], "autoinstall.yaml"), "w") as f:
    json.dump(config, f, ensure_ascii=False, indent=1)
PY

# 4. The new image: the stock one plus two entries, boot records replayed as they were.
echo "== собираю $OUT"
rm -f "$OUT"
# xorriso's own exit status decides (pipefail): a disk filled halfway leaves a file that is not an image
if ! xorriso -indev "$ISO" -outdev "$OUT" \
  -map "$WORK/autoinstall.yaml" /autoinstall.yaml \
  -map "$WORK/orpheus" /orpheus \
  "${MODEL_MAPS[@]}" "${PACKAGE_MAPS[@]}" \
  -compliance iso_9660_level=3 \
  -boot_image any replay 2>&1 | { grep -E "^xorriso : (FAILURE|SORRY|WARNING)|Writing to" | grep -v -- "-volid text" || true; }; then
  rm -f "$OUT"
  echo "не получилось собрать образ (xorriso завершился с ошибкой)"; exit 1
fi
[ -s "$OUT" ] || { echo "не получилось собрать образ"; exit 1; }
echo "образ: $OUT ($(du -h "$OUT" | cut -f1))"

# 5. Optionally write it to the stick.
if [ -n "$DEVICE" ]; then
  [ -b "$DEVICE" ] || { echo "$DEVICE — не диск"; exit 1; }
  [ "$(lsblk -dno TYPE "$DEVICE")" = disk ] || { echo "$DEVICE — раздел; нужен весь диск, например /dev/sdb"; exit 1; }
  if [ "$(lsblk -dno RM "$DEVICE" | tr -d ' ')" != 1 ] && [ "$(lsblk -dno TRAN "$DEVICE")" != usb ]; then
    echo "$DEVICE не похож на флешку (не съёмный и не USB) — не трогаю"; exit 1
  fi
  if [ "$(stat -c %s "$OUT")" -gt "$(lsblk -bdno SIZE "$DEVICE")" ]; then
    echo "образ ($(du -h "$OUT" | cut -f1)) больше флешки"; exit 1
  fi
  lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS "$DEVICE"
  read -rp "ВСЁ на $DEVICE будет стёрто. Напиши имя устройства ещё раз для подтверждения: " again
  [ "$again" = "$DEVICE" ] || { echo "отмена"; exit 1; }
  for part in $(lsblk -lnpo NAME "$DEVICE" | tail -n +2); do sudo umount "$part" 2>/dev/null || true; done
  sudo dd if="$OUT" of="$DEVICE" bs=4M status=progress conv=fsync
  sync
  echo "флешка готова"
fi
