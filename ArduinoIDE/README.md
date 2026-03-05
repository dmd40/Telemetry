# ESP Scripts

ESP8266 sketches and Arduino IDE files for telemetry upload/relay testing.

## Contents
- `esp8266_vehicle_telemetry/esp8266_vehicle_telemetry.ino`
- `esp8266_vehicle_telemetry.ino`
- `arduinoIDEcode.cpp`

## Purpose
- Connect ESP8266 to network
- Read telemetry source data
- POST to backend ingest endpoint (for example `/api/ingest`)

## Notes
- Keep endpoint URL and Wi-Fi credentials aligned with deployment target.
- Use stable CP210x COM assignment on Windows if upload ports conflict.
