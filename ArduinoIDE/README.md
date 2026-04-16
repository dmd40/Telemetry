# ESP Scripts

ESP8266 sketches and Arduino IDE files for telemetry upload/relay testing.

## Contents
- `esp8266_vehicle_telemetry/esp8266_vehicle_telemetry.ino`
- `esp8266_vehicle_telemetry.ino`
- `arduinoIDEcode.cpp`
- `i2c_scan_example.ino`

## Purpose
- Connect ESP8266 to network
- Read telemetry source data
- POST to backend ingest endpoint (for example `/api/ingest`)

## Current wiring
- Cycle Analyst serial input on ESP `D7` through a level shifter, with USB `Serial` reserved for console/debug output
- GT-U7 GPS TX on `D5`
- OLED on `D2` SDA and `D1` SCL
- Shared ground across CA, GPS, OLED, and ESP
- Build target used here: `esp8266:esp8266:nodemcuv2`

## On-screen status
- Line 1: hotspot name
- Line 2: rotating telemetry metric
- Line 3: age / queue counters
- Line 4: parser state (`CA:OK` or `CA:WAIT`) and GPS fix state

## Notes
- Keep endpoint URL and Wi-Fi credentials aligned with deployment target.
- Use stable CP210x COM assignment on Windows if upload ports conflict.
