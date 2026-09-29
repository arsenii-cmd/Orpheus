#!/usr/bin/env bash
# Backlight off while the lid is closed, on when it opens (the machine itself keeps running).
# Run by orpheus-lid.service, installed by scripts/laptop.sh.
shopt -s nullglob
last=""
while true; do
  state="open"
  for f in /proc/acpi/button/lid/*/state; do
    grep -q closed "$f" && state="closed"
  done
  if [ "$state" != "$last" ]; then
    for b in /sys/class/backlight/*; do
      if [ "$state" = closed ]; then echo 4 > "$b/bl_power"; else echo 0 > "$b/bl_power"; fi
    done
    last="$state"
  fi
  sleep 1
done
