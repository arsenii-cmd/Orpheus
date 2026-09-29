# A USB stick that installs Ubuntu Server with Orpheus

[Русский](usb.md) · **English**

`scripts/make_usb.sh` takes the official Ubuntu Server 24.04 image and adds answers for the
installer, Orpheus itself and its models: speech recognition, the voice and two language models
(~4.6 GB): Qwen3‑4B (default) and Qwen2.5‑3B (faster but weaker). Plus system packages, Python
libraries and Ollama itself (without CUDA, ~40 MB), so **the laptop needs no internet at all**:
neither during installation nor on first boot. The whole image is about 8 GB.

- `ORPHEUS_BUNDLE_MODELS=0` — no models, the laptop downloads them on first boot.
- `ORPHEUS_BUNDLE_PACKAGES=0` — no packages and Ollama, the laptop downloads them too.

The image holds no secrets. The network (Wi-Fi and its password), the user with a password and SSH
are entered on the installer's usual screens right on the laptop. Everything else is automatic:
partitioning, copying Orpheus and setup on first boot.

## Requirements

- A computer with Linux (or WSL on Windows), internet and `xorriso curl python3 pip git zstd`:
  Ubuntu/Debian — `sudo apt install xorriso curl python3-pip git zstd`,
  Arch — `sudo pacman -S --needed libisoburn curl python-pip git zstd`.
- A 16 GB stick (32 GB to be safe). **Everything on it will be erased.**

## Building

From the repository root:

```sh
lsblk                          # find the stick by its size, e.g. /dev/sdb
scripts/make_usb.sh /dev/sdb   # build the image and write it to the stick
```

The script asks only for the time zone (`Europe/Moscow` by default) and a GitHub login whose public
SSH keys to put on the SSH screen.

The official image (~3 GB), models, packages and Ollama are downloaded once into
`~/.cache/orpheus-usb` and verified by checksums, so a rebuild takes a minute. An interrupted download
resumes where it stopped. Before writing, the script shows the stick and asks you to type the device
name again. It won't touch the system disk: it writes only to removable and USB devices.

Without `/dev/sdX` the script only builds `orpheus-ubuntu-server.iso`. It can also be written from
Windows with [Rufus](https://rufus.ie) — choose **DD** when asked about the mode.

## Installing

1. Insert the stick into the Orpheus laptop and boot from it (F12 / F9 / Esc — boot menu).
2. Pick "Try or Install Ubuntu Server". If the installer asks
   "Continue with autoinstall? (yes|no)", type `yes`.
3. **Network** — connect a cable or pick Wi-Fi and enter its password (needed for `ssh` and updates;
   installing Orpheus doesn't need the network, so it can be skipped). Done.
4. **Profile** — name, machine name (`orpheus`, on the network it will be `orpheus.local`),
   login and password. Done.
5. **SSH** — "Install OpenSSH server" is already ticked, the GitHub keys are already filled in. Done.
6. ⚠️ Next the installer **erases the laptop's whole internal disk by itself** and installs the system.
   After 10–20 minutes the laptop reboots. Remove the stick.
7. First boot (5–15 minutes): system packages, Python libraries, Ollama and the models are installed
   from what was on the stick. The log runs on screen; from another computer:
   `ssh login@orpheus.local`, then `journalctl -u orpheus-firstboot -f`.
8. At the end the laptop reboots once more — and Orpheus starts by itself.

If the first boot was interrupted, it runs again on the next boot.

## What ends up on the laptop

- Ubuntu Server 24.04 on the whole disk (no LVM), OpenSSH, the user created during installation.
- `~/Orpheus` — the code as it was when the stick was built (uncommitted changes included).
- Everything from `scripts/laptop.sh`: the lid doesn't suspend, zram, time zone, Cyrillic in the console.
- Speech models, Ollama tuned for speed, the language model.
- The `orpheus` service (voice mode), started at boot.

Checking after installation:

```sh
cd ~/Orpheus && . .venv/bin/activate
python -m orpheus --stats chat
python scripts/bench_llm.py
journalctl --user -u orpheus -f
```

## Comparing the two language models

```sh
cd ~/Orpheus && . .venv/bin/activate
python scripts/bench_llm.py                                          # Qwen3-4B
ORPHEUS_MODEL=qwen2.5-3b-abliterated python scripts/bench_llm.py     # Qwen2.5-3B
ORPHEUS_MODEL=qwen2.5-3b-abliterated python -m orpheus --stats chat  # talk, check the memory
```

To always run Orpheus on Qwen2.5‑3B, run `systemctl --user edit orpheus`, add `[Service]` and the line
`Environment=ORPHEUS_MODEL=qwen2.5-3b-abliterated`, then `systemctl --user restart orpheus`.
