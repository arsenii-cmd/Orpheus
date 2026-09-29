#!/usr/bin/env bash
# The laptop's firewall (ufw): nothing comes in except
#   SSH (22) from the home LAN,
#   the phone server (8765) from the VPN tunnel's end (a reverse proxy elsewhere) and from the home LAN.
# Safe to run again; SSH is allowed before the firewall goes up, so the session survives.
#   sudo scripts/firewall.sh
set -euo pipefail
LAN="${ORPHEUS_LAN:-192.168.2.0/24}"
TUNNEL="${ORPHEUS_TUNNEL:-10.8.0.1}"
PORT="${ORPHEUS_PORT:-8765}"

command -v ufw >/dev/null || apt-get install -y ufw
ufw --force reset >/dev/null           # a known state, no leftovers
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$LAN" to any port 22 proto tcp comment "ssh from home"
ufw allow from "$LAN" to any port "$PORT" proto tcp comment "orpheus from home"
ufw allow from "$TUNNEL" to any port "$PORT" proto tcp comment "orpheus via the tunnel"
ufw --force enable
ufw status verbose
