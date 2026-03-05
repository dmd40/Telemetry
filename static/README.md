# Front End

Static web UI served by the backend.

## Pages
- `index.html`: live dashboard (gauges + charts)
- `lap.html`: per-lap time series view
- `gps.html`: lap trajectory map + live path updates
- `logs.html`: raw/grouped log query UI

## Assets
- `css/styles.css`: shared styling/theme
- `logo2.png`: header branding

## Runtime Data Source
- Pulls API data from same host:
  - HTTP endpoints under `/api/*`
  - WebSocket stream at `/ws`
