# Установка Ubuntu Server на ноутбук Орфея

Это ручной путь. Есть автоматический: флешка, которая ставит систему сразу с Орфеем —
[usb.md](usb.md).

Версия: **Ubuntu Server 24.04 LTS**. Под её Python 3.12 есть готовые сборки всех
библиотек Орфея. В 26.04 Python новее, и сборки sherpa-onnx под него могут запаздывать.

## 1. Загрузочная флешка

Нужна флешка от 4 ГБ, всё на ней сотрётся. Скачай
`ubuntu-24.04.x-live-server-amd64.iso` с <https://ubuntu.com/download/server>.

**Windows:** [Rufus](https://rufus.ie). Выбираешь флешку и ISO, схема разделов GPT,
на вопрос о режиме — «ISO», жмёшь «Старт».

**Linux:**

```sh
lsblk     # найди флешку по размеру, например /dev/sdb
sudo dd if=ubuntu-24.04*-live-server-amd64.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

Трижды проверь `/dev/sdX`: `dd` без вопросов затрёт любой диск.

## 2. Загрузка с флешки

Вставь флешку, включи ноут и сразу жми клавишу меню загрузки: обычно F12,
у HP — F9, у ASUS — Esc, у Lenovo — F12 или кнопка Novo. Выбери флешку, затем
«Try or Install Ubuntu Server». Secure Boot отключать не нужно.

Если в BIOS есть настройка **UMA Frame Buffer Size**, поставь минимум: всё, что
отдано видеокарте, отнимается у модели.

## 3. Установщик

| Экран | Что выбрать |
| --- | --- |
| Language | English: в консоли так проще, русский в Орфее это не затрагивает |
| Keyboard | English (US) |
| Type of install | Ubuntu Server (не minimized) |
| Network | Лучше кабелем; Wi-Fi тоже можно, если адаптер определился |
| Proxy, Mirror | Оставить как есть |
| Storage | Use an entire disk. **Подвох:** по умолчанию раздел `ubuntu-lv` получает не весь диск. На экране Storage configuration выбери `ubuntu-lv` → Edit → максимальный Size. Либо сними галку «Set up this disk as an LVM group» |
| Profile | Имя, hostname `orpheus`, логин, пароль |
| Ubuntu Pro | Skip |
| SSH | **Install OpenSSH server**. «Import SSH key → from GitHub» с логином `arsenii-cmd` пустит тебя по твоему ключу без пароля |
| Featured snaps | Ничего |

После «Reboot Now» вытащи флешку и нажми Enter.

## 4. Первый вход

С основного ноута:

```sh
ssh логин@orpheus.local     # или по IP: на самом ноуте его покажет `ip a`
```

```sh
sudo apt update && sudo apt full-upgrade -y
```

### Wi-Fi, если не настроил его в установщике

Файл `/etc/netplan/50-wifi.yaml` (имя интерфейса смотри в `ip a`, обычно `wlp1s0`):

```yaml
network:
  version: 2
  wifis:
    wlp1s0:
      dhcp4: true
      access-points:
        "ИмяСети":
          password: "пароль"
```

```sh
sudo chmod 600 /etc/netplan/50-wifi.yaml
sudo netplan apply
```

## 5. Орфей

Пока код лежит в ветке, а не в `main`:

```sh
git clone https://github.com/arsenii-cmd/Orpheus.git
cd Orpheus
sudo scripts/laptop.sh Europe/Moscow   # ноут как сервер (см. ниже)
scripts/install.sh                     # сам Орфей, модели, Ollama
sudo reboot
```

Если репозиторий приватный, `git clone` попросит логин, а вместо пароля нужен
токен GitHub. Проще сделать на ноуте ключ (`ssh-keygen -t ed25519`), добавить
`~/.ssh/id_ed25519.pub` в GitHub → Settings → SSH keys и клонировать
`git@github.com:arsenii-cmd/Orpheus.git`.

`scripts/laptop.sh` можно запускать повторно, он меняет только то, что ещё не настроено:

- закрытая крышка не усыпляет ноут;
- экран консоли гаснет через минуту бездействия (с перезагрузки);
- zram — сжатая подкачка в оперативной памяти, запас, когда модели и речи тесно в 8 ГБ;
- часовой пояс (по нему Орфей называет время и даты);
- кириллица в текстовой консоли (шрифт Terminus).

## 6. Проверка

```sh
cd Orpheus && . .venv/bin/activate
python -m orpheus --stats chat     # мозг и память
python scripts/bench_llm.py        # задержки с кэшем и без
python -m orpheus --stats voice    # голосом
swapon                             # должна быть видна /dev/zram0
```
