#!/usr/bin/env bash
set -euo pipefail

HOTSPOT1_SSID="${HOTSPOT1_SSID:-Wing Stop}"
HOTSPOT1_PASS="${HOTSPOT1_PASS:-MyDogIsThick}"
HOTSPOT2_SSID="${HOTSPOT2_SSID:-Driver2Hotspot}"
HOTSPOT2_PASS="${HOTSPOT2_PASS:-Driver2Password}"
PING_TARGET="${PING_TARGET:-1.1.1.1}"
CHECK_INTERVAL_SEC="${CHECK_INTERVAL_SEC:-10}"
CONNECT_TIMEOUT_SEC="${CONNECT_TIMEOUT_SEC:-20}"
ACTIVE_LOG="${ACTIVE_LOG:-}"
NMCLI=(sudo -n nmcli)

log() {
  local msg="$1"
  if [[ -n "$ACTIVE_LOG" ]]; then
    printf '%s %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$msg" >> "$ACTIVE_LOG"
  fi
  printf '%s\n' "$msg"
}

require_nmcli() {
  if ! command -v nmcli >/dev/null 2>&1; then
    log "[wifi] nmcli not found; install or enable NetworkManager on this Pi."
    exit 1
  fi
  if ! sudo -n true >/dev/null 2>&1; then
    log "[wifi] passwordless sudo is required for NetworkManager profile management."
    exit 1
  fi
}

default_iface() {
  "${NMCLI[@]}" -t -f DEVICE,TYPE,STATE dev status | awk -F: '$2=="wifi" {print $1; exit}'
}

ethernet_connected() {
  "${NMCLI[@]}" -t -f DEVICE,TYPE,STATE dev status | awk -F: '$2=="ethernet" && $3=="connected" {found=1} END {exit !found}'
}

ensure_profile() {
  local con_name="$1"
  local ssid="$2"
  local pass="$3"

  if ! "${NMCLI[@]}" -t -f NAME con show | grep -Fxq "$con_name"; then
    "${NMCLI[@]}" con add type wifi ifname "*" con-name "$con_name" ssid "$ssid" >/dev/null
    "${NMCLI[@]}" con modify "$con_name" wifi-sec.key-mgmt wpa-psk >/dev/null
    "${NMCLI[@]}" con modify "$con_name" wifi-sec.psk "$pass" >/dev/null
    "${NMCLI[@]}" con modify "$con_name" connection.autoconnect yes >/dev/null
  else
    "${NMCLI[@]}" con modify "$con_name" 802-11-wireless.ssid "$ssid" >/dev/null
    "${NMCLI[@]}" con modify "$con_name" wifi-sec.key-mgmt wpa-psk >/dev/null
    "${NMCLI[@]}" con modify "$con_name" wifi-sec.psk "$pass" >/dev/null
    "${NMCLI[@]}" con modify "$con_name" connection.autoconnect yes >/dev/null
  fi
}

ensure_priorities() {
  "${NMCLI[@]}" con modify "Telemetry Hotspot 1" connection.autoconnect-priority 20 >/dev/null 2>&1 || true
  "${NMCLI[@]}" con modify "Telemetry Hotspot 2" connection.autoconnect-priority 10 >/dev/null 2>&1 || true
}

connected_ssid() {
  local iface="$1"
  "${NMCLI[@]}" -t -f GENERAL.CONNECTION dev show "$iface" 2>/dev/null | sed 's/^GENERAL.CONNECTION://'
}

activate_profile() {
  local iface="$1"
  local con_name="$2"
  log "[wifi] trying $con_name on $iface"
  "${NMCLI[@]}" con up "$con_name" ifname "$iface" --wait "$CONNECT_TIMEOUT_SEC" >/dev/null 2>&1 || return 1
  return 0
}

main() {
  require_nmcli

  local iface="${1:-}"
  if [[ -z "$iface" ]]; then
    iface="$(default_iface)"
  fi
  if [[ -z "$iface" ]]; then
    log "[wifi] no Wi-Fi interface found."
    exit 1
  fi

  ensure_profile "Telemetry Hotspot 1" "$HOTSPOT1_SSID" "$HOTSPOT1_PASS"
  ensure_profile "Telemetry Hotspot 2" "$HOTSPOT2_SSID" "$HOTSPOT2_PASS"
  ensure_priorities

  log "[wifi] monitoring $iface for $HOTSPOT1_SSID / $HOTSPOT2_SSID"

  while true; do
    if ethernet_connected; then
      sleep "$CHECK_INTERVAL_SEC"
      continue
    fi

    if "${NMCLI[@]}" -t -f DEVICE,TYPE,STATE dev status | awk -F: '$2=="wifi" && $3=="connected" {found=1} END {exit !found}'; then
      sleep "$CHECK_INTERVAL_SEC"
      continue
    fi

    log "[wifi] connection lost or offline; cycling hotspots"
    "${NMCLI[@]}" dev disconnect "$iface" >/dev/null 2>&1 || true
    activate_profile "$iface" "Telemetry Hotspot 1" || true
    sleep 4
    if can_reach_internet "$iface"; then
      sleep "$CHECK_INTERVAL_SEC"
      continue
    fi

    "${NMCLI[@]}" dev disconnect "$iface" >/dev/null 2>&1 || true
    activate_profile "$iface" "Telemetry Hotspot 2" || true
    sleep 4
    if can_reach_internet "$iface"; then
      sleep "$CHECK_INTERVAL_SEC"
      continue
    fi

    sleep "$CHECK_INTERVAL_SEC"
  done
}

main "$@"
