# Installing Ubuntu Server on the Orpheus laptop

[Русский](install-ubuntu-server.md) · **English**

This is the manual way. There is an automatic one: a USB stick that installs the system together with
Orpheus — [usb.en.md](usb.en.md).

Version: **Ubuntu Server 24.04 LTS**. All of Orpheus's libraries have prebuilt wheels for its
Python 3.12. 26.04 ships a newer Python, and sherpa-onnx builds for it may lag behind.

## 1. Boot stick

You need a stick of 4 GB or more; everything on it will be erased. Download
`ubuntu-24.04.x-live-server-amd64.iso` from <https://ubuntu.com/download/server>.

**Windows:** [Rufus](https://rufus.ie). Pick the stick and the ISO, GPT partition scheme, answer
"ISO" when asked about the mode, press "Start".

**Linux:**

```sh
lsblk     # find the stick by its size, e.g. /dev/sdb
sudo dd if=ubuntu-24.04*-live-server-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Check `/dev/sdX` three times: `dd` overwrites any disk without asking.

## 2. Booting from the stick

Insert the stick, turn the laptop on and immediately press the boot menu key: usually F12, F9 on HP,
Esc on ASUS, F12 or the Novo button on Lenovo. Pick the stick, then "Try or Install Ubuntu Server".
There's no need to disable Secure Boot.

If the BIOS has a **UMA Frame Buffer Size** setting, set it to the minimum: whatever goes to the GPU
is taken from the model.

## 3. Installer

| Screen | What to choose |
| --- | --- |
| Language | English |
| Keyboard | English (US) |
| Type of install | Ubuntu Server (not minimized) |
| Network | Cable is best; Wi-Fi works too if the adapter was detected |
| Proxy, Mirror | Leave as is |
| Storage | Use an entire disk. **Gotcha:** by default the `ubuntu-lv` volume doesn't get the whole disk. On the Storage configuration screen pick `ubuntu-lv` → Edit → maximum Size. Or untick "Set up this disk as an LVM group" |
| Profile | Name, hostname `orpheus`, login, password |
| Ubuntu Pro | Skip |
| SSH | **Install OpenSSH server**. "Import SSH key → from GitHub" with your GitHub login lets you in with your key, no password |
| Featured snaps | None |

After "Reboot Now" remove the stick and press Enter.

## 4. First login

From another computer:

```sh
ssh login@orpheus.local     # or by IP: `ip a` on the laptop shows it
```

```sh
sudo apt update && sudo apt full-upgrade -y
```

### Wi-Fi, if it wasn't set up in the installer

The file `/etc/netplan/50-wifi.yaml` (see the interface name in `ip a`, usually `wlp1s0`):

```yaml
network:
  version: 2
  wifis:
    wlp1s0:
      dhcp4: true
      access-points:
        "NetworkName":
          password: "password"
```

```sh
sudo chmod 600 /etc/netplan/50-wifi.yaml
sudo netplan apply
```

## 5. Orpheus

```sh
git clone https://github.com/arsenii-cmd/Orpheus.git
cd Orpheus
sudo scripts/laptop.sh Europe/Moscow   # the laptop as a server (see below)
scripts/install.sh                     # Orpheus itself, models, Ollama
sudo reboot
```

`scripts/laptop.sh` can be run again; it only changes what isn't set up yet:

- a closed lid doesn't put the laptop to sleep;
- the console screen blanks after a minute of inactivity (from the next boot);
- zram — compressed swap in RAM, a reserve for when the model and speech are tight in 8 GB;
- the time zone (Orpheus tells the time and dates by it);
- Cyrillic in the text console (the Terminus font).

## 6. Checking

```sh
cd Orpheus && . .venv/bin/activate
python -m orpheus --stats chat     # brain and memory
python scripts/bench_llm.py        # latency with and without the cache
python -m orpheus --stats voice    # by voice
swapon                             # /dev/zram0 should be listed
```
