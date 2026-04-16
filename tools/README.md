# Tools

Launcher and utility scripts.

## Telemetry launchers
- `Start Telemetry.cmd`
- `Stop Telemetry.cmd`
- `start_telemetry.ps1`
- `stop_telemetry.ps1`
- `ca_reader.ps1`
- `start_vehicle_pi.sh`
- `stop_vehicle_pi.sh`

These start/stop the local backend service and optional Funnel exposure.

## Serial reader
- `ca_reader.ps1` reads the Cycle Analyst stream from a serial port
- Defaults to `COM3` at `9600` baud
- Override with `CA_PORT` and `CA_BAUD`
- Writes a daily TSV log to `logs/ca_reader_YYYY-MM-DD.tsv`

## Vehicle Pi launcher
- `start_vehicle_pi.sh` starts the backend with `TELEM_SERIAL_MODE=vehicle`
- Auto-detects CA + GPS USB serial ports unless `TELEM_CA_PORT` / `TELEM_GPS_PORT` are set
- Starts `vehicle_wifi_failover.sh` in the background when `nmcli` is available
- `stop_vehicle_pi.sh` stops the backend and disables Funnel if present

## Vehicle Wi-Fi failover
- `vehicle_wifi_failover.sh` manages the two driver hotspots used by the ESP setup
- Default SSIDs/passwords are the same as the ESP:
  - `Wing Stop` / `MyDogIsThick`
  - `Driver2Hotspot` / `Driver2Password`
- It creates NetworkManager profiles, prefers hotspot 1, and falls back to hotspot 2 if connectivity drops
- It uses `sudo -n nmcli` so the Pi can create and switch NetworkManager profiles without an interactive prompt
- If the Pi has a connected ethernet link, the helper stays idle instead of thrashing Wi-Fi
- Once a Wi-Fi hotspot is connected, the helper keeps it up and only retries when the Wi-Fi device itself disconnects
- Override names/passwords with `HOTSPOT1_SSID`, `HOTSPOT1_PASS`, `HOTSPOT2_SSID`, `HOTSPOT2_PASS`

## Binaries
- `cloudflared.exe` (local utility binary, ignored by git)
