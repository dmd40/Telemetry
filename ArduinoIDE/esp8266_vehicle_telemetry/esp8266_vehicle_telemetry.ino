#include <Arduino.h>
#include <ESP8266WiFi.h>
#include <ESP8266WebServer.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecureBearSSL.h>
#include <SoftwareSerial.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// -------- OLED --------
#define OLED_SDA D3
#define OLED_SCL D1
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_ADDR 0x3C
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, -1);
bool oledReady = false;

// -------- Telemetry input (Cycle Analyst) --------
// CA TX is 0-5V TTL. Level-shift CA TX down to 3.3V before ESP RX.
// CA stream: 9600 baud, tab-delimited, header + data rows.
#define CA_RX_PIN D6
#define CA_BAUD 9600
SoftwareSerial caSerial(CA_RX_PIN, -1);  // RX only

const int CA_MAX_FIELDS = 24;
String caFields[CA_MAX_FIELDS];
bool caHeaderParsed = false;
int caIdxAh = -1;
int caIdxV = -1;
int caIdxA = -1;
int caIdxS = -1;
int caIdxNm = -1;

struct TelemetryData {
  float volts;
  float amps;
  float mph;
  float torque;
  float ampHours;
  unsigned long lastUpdateMs;
  unsigned long rowsParsed;
  unsigned long rowsRejected;
};

TelemetryData data = {0, 0, 0, 0, 0, 0, 0, 0};

struct TelemetrySample {
  uint32_t tMs;
  float volts;
  float amps;
  float mph;
  float torque;
  float ampHours;
};

const uint16_t SAMPLE_QUEUE_CAP = 256;
TelemetrySample sampleQueue[SAMPLE_QUEUE_CAP];
uint16_t sampleQueueTail = 0;
uint16_t sampleQueueCount = 0;
uint32_t sampleQueueDropped = 0;
uint32_t samplePostOk = 0;
uint32_t samplePostFail = 0;

// -------- Wi-Fi failover --------
struct Hotspot {
  const char* ssid;
  const char* pass;
};

Hotspot hotspots[] = {
  {"Wing Stop", "MyDogIsThick"},
  {"Driver2Hotspot", "Driver2Password"}
};

const int HOTSPOT_COUNT = sizeof(hotspots) / sizeof(hotspots[0]);
int hotspotIndex = 0;
bool wifiAttemptInProgress = false;
unsigned long wifiAttemptStartMs = 0;
const unsigned long WIFI_ATTEMPT_TIMEOUT_MS = 12000;
const unsigned long WIFI_RETRY_GAP_MS = 1200;
unsigned long wifiRetryStartMs = 0;
bool waitBeforeRetry = false;

// -------- Optional upstream relay --------
// Public ingest endpoint from Telemetry-main backend.
// Example: "https://your-public-host.example.com/api/ingest"
const char* SERVER_POST_URL = "https://laaeit.tailb3a100.ts.net/api/ingest";
// Optional shared token. Leave empty to disable token auth.
const char* INGEST_TOKEN = "";
const unsigned long POST_INTERVAL_MS = 500;
const unsigned long BACKLOG_POST_INTERVAL_MS = 120;
const uint16_t HTTP_TIMEOUT_MS = 800;
unsigned long lastPostMs = 0;
WiFiClient plainClient;
BearSSL::WiFiClientSecure secureClient;

// -------- Local lightweight web server --------
ESP8266WebServer web(80);

// -------- Display rotation --------
uint8_t screenIndex = 0;
const uint8_t TOTAL_SCREENS = 5;
unsigned long lastScreenSwitchMs = 0;
const unsigned long SCREEN_HOLD_MS = 3000;

void drawHeader(const char* title) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print(title);
  display.drawLine(0, 10, SCREEN_WIDTH - 1, 10, SSD1306_WHITE);
}

void drawMetric(const char* label, float value, const char* unit) {
  display.setTextSize(2);
  display.setCursor(0, 18);
  display.print(label);
  display.setCursor(0, 42);
  display.print(value, 1);
  display.print(" ");
  display.print(unit);
}

String wifiStateText() {
  if (WiFi.status() == WL_CONNECTED) return "CONNECTED";
  if (wifiAttemptInProgress) return "CONNECTING";
  if (waitBeforeRetry) return "RETRY WAIT";
  return "DISCONNECTED";
}

const char* wifiStateShort() {
  if (WiFi.status() == WL_CONNECTED) return "CONN";
  if (wifiAttemptInProgress) return "TRY";
  if (waitBeforeRetry) return "WAIT";
  return "DISC";
}

void captureLatestSample(TelemetrySample& s) {
  s.tMs = data.lastUpdateMs == 0 ? millis() : data.lastUpdateMs;
  s.volts = data.volts;
  s.amps = data.amps;
  s.mph = data.mph;
  s.torque = data.torque;
  s.ampHours = data.ampHours;
}

void enqueueSample(const TelemetrySample& s) {
  if (sampleQueueCount >= SAMPLE_QUEUE_CAP) {
    sampleQueueTail = (sampleQueueTail + 1) % SAMPLE_QUEUE_CAP;
    sampleQueueCount--;
    sampleQueueDropped++;
  }

  uint16_t insertIdx = (sampleQueueTail + sampleQueueCount) % SAMPLE_QUEUE_CAP;
  sampleQueue[insertIdx] = s;
  sampleQueueCount++;
}

void queueCurrentSampleIfOffline() {
  if (WiFi.status() == WL_CONNECTED) return;
  TelemetrySample s;
  captureLatestSample(s);
  enqueueSample(s);
}

String metricLine() {
  switch (screenIndex) {
    case 0:
      return "V=" + String(data.volts, 1) + "V";
    case 1:
      return "A=" + String(data.amps, 1) + "A";
    case 2:
      return "MPH=" + String(data.mph, 1);
    case 3:
      return "TQ=" + String(data.torque, 1) + "Nm";
    default:
      return "Ah=" + String(data.ampHours, 2);
  }
}

void renderScreen() {
  if (!oledReady) return;

  String activeSsid;
  if (WiFi.status() == WL_CONNECTED && WiFi.SSID().length() > 0) {
    activeSsid = WiFi.SSID();
  } else {
    activeSsid = hotspots[hotspotIndex].ssid;
  }

  if (activeSsid.length() > 14) activeSsid = activeSsid.substring(0, 14);

  const unsigned long ageSec = (data.lastUpdateMs == 0) ? 0 : ((millis() - data.lastUpdateMs) / 1000);

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);

  // Line 1: active/target hotspot
  display.setCursor(0, 0);
  display.print("HS:");
  display.print(activeSsid);

  // Line 2: one telemetry metric (rotating)
  display.setCursor(0, 22);
  display.print(metricLine());

  // Line 3: quick status + data age
  display.setCursor(0, 44);
  display.print("Age:");
  display.print(ageSec);
  display.print("s Q:");
  display.print(sampleQueueCount);
  display.print(" D:");
  display.print(sampleQueueDropped);

  display.display();
}

void startWifiAttempt(int idx) {
  hotspotIndex = idx % HOTSPOT_COUNT;
  WiFi.disconnect(true);
  delay(60);
  WiFi.begin(hotspots[hotspotIndex].ssid, hotspots[hotspotIndex].pass);
  wifiAttemptInProgress = true;
  wifiAttemptStartMs = millis();
  waitBeforeRetry = false;
}

void startNextHotspotAttempt() {
  hotspotIndex = (hotspotIndex + 1) % HOTSPOT_COUNT;
  startWifiAttempt(hotspotIndex);
}

void serviceWifi() {
  wl_status_t st = WiFi.status();

  if (st == WL_CONNECTED) {
    wifiAttemptInProgress = false;
    waitBeforeRetry = false;
    return;
  }

  if (waitBeforeRetry) {
    if (millis() - wifiRetryStartMs >= WIFI_RETRY_GAP_MS) {
      startNextHotspotAttempt();
    }
    return;
  }

  if (!wifiAttemptInProgress) {
    startWifiAttempt(hotspotIndex);
    return;
  }

  if (millis() - wifiAttemptStartMs >= WIFI_ATTEMPT_TIMEOUT_MS) {
    WiFi.disconnect(true);
    wifiAttemptInProgress = false;
    waitBeforeRetry = true;
    wifiRetryStartMs = millis();
  }
}

void handleRoot() {
  String html;
  html.reserve(900);
  html += "<!doctype html><html><head><meta charset='utf-8'><title>EV Telemetry</title>";
  html += "<meta name='viewport' content='width=device-width,initial-scale=1'>";
  html += "<style>body{font-family:system-ui;padding:16px} .k{font-weight:700} .v{font-size:1.4rem} .row{margin:.5rem 0}</style>";
  html += "</head><body><h2>EV Telemetry (ESP8266)</h2>";
  html += "<div class='row'><span class='k'>WiFi:</span> " + wifiStateText() + "</div>";
  html += "<div class='row'><span class='k'>Hotspot:</span> " + String(hotspots[hotspotIndex].ssid) + "</div>";
  html += "<div class='row'><span class='k'>V:</span> <span id='V' class='v'>--</span></div>";
  html += "<div class='row'><span class='k'>A:</span> <span id='A' class='v'>--</span></div>";
  html += "<div class='row'><span class='k'>mph:</span> <span id='MPH' class='v'>--</span></div>";
  html += "<div class='row'><span class='k'>TQ:</span> <span id='TQ' class='v'>--</span></div>";
  html += "<div class='row'><span class='k'>Ah:</span> <span id='AH' class='v'>--</span></div>";
  html += "<script>async function tick(){try{const r=await fetch('/api/latest');const j=await r.json();";
  html += "V.textContent=(j.V??0).toFixed(1);A.textContent=(j.A??0).toFixed(1);";
  html += "MPH.textContent=(j.mph??0).toFixed(1);TQ.textContent=(j.torque??0).toFixed(1);";
  html += "AH.textContent=(j.Ah??0).toFixed(2);}catch(e){}} setInterval(tick,500); tick();</script>";
  html += "</body></html>";
  web.send(200, "text/html", html);
}

void handleApiLatest() {
  String json;
  json.reserve(220);
  json += "{";
  json += "\"V\":" + String(data.volts, 2) + ",";
  json += "\"A\":" + String(data.amps, 2) + ",";
  json += "\"mph\":" + String(data.mph, 2) + ",";
  json += "\"torque\":" + String(data.torque, 2) + ",";
  json += "\"Ah\":" + String(data.ampHours, 3) + ",";
  json += "\"ssid\":\"" + String(hotspots[hotspotIndex].ssid) + "\",";
  json += "\"wifi\":\"" + wifiStateText() + "\",";
  json += "\"queue\":" + String(sampleQueueCount) + ",";
  json += "\"queueDropped\":" + String(sampleQueueDropped) + ",";
  json += "\"t_ms\":" + String(millis());
  json += "}";
  web.send(200, "application/json", json);
}

void setupWeb() {
  web.on("/", handleRoot);
  web.on("/api/latest", handleApiLatest);
  web.begin();
}

bool postSample(const TelemetrySample& s, bool fromQueue) {
  HTTPClient http;
  http.setTimeout(HTTP_TIMEOUT_MS);
  String url = String(SERVER_POST_URL);

  bool beginOk = false;
  if (url.startsWith("https://")) {
    secureClient.setTimeout(HTTP_TIMEOUT_MS);
    beginOk = http.begin(secureClient, SERVER_POST_URL);
  } else {
    plainClient.setTimeout(HTTP_TIMEOUT_MS);
    beginOk = http.begin(plainClient, SERVER_POST_URL);
  }
  if (!beginOk) return false;

  http.addHeader("Content-Type", "application/json");
  if (INGEST_TOKEN[0] != '\0') {
    http.addHeader("X-Ingest-Token", INGEST_TOKEN);
  }

  String payload;
  payload.reserve(240);
  payload += "{";
  payload += "\"t\":" + String(s.tMs) + ",";
  payload += "\"lap\":0,";
  payload += "\"V\":" + String(s.volts, 2) + ",";
  payload += "\"A\":" + String(s.amps, 2) + ",";
  payload += "\"mph\":" + String(s.mph, 2) + ",";
  payload += "\"torque\":" + String(s.torque, 2) + ",";
  payload += "\"Ah\":" + String(s.ampHours, 3) + ",";
  payload += "\"source\":\"cycle_analyst\",";
  payload += "\"buffered\":" + String(fromQueue ? "true" : "false");
  payload += "}";

  int code = http.POST(payload);
  bool ok = code >= 200 && code < 300;
  if (ok) samplePostOk++;
  else samplePostFail++;

  Serial.print("POST ");
  Serial.print(fromQueue ? "[Q] " : "[L] ");
  Serial.print(SERVER_POST_URL);
  Serial.print(" -> ");
  Serial.print(code);
  Serial.print(" q=");
  Serial.println(sampleQueueCount);

  http.end();
  return ok;
}

void maybePostUpstream() {
  if (SERVER_POST_URL[0] == '\0') return;
  if (String(SERVER_POST_URL).indexOf("REPLACE_WITH_PUBLIC_HOST") >= 0) return;
  if (WiFi.status() != WL_CONNECTED) return;
  if (data.lastUpdateMs == 0 && sampleQueueCount == 0) return;

  unsigned long intervalMs = sampleQueueCount > 0 ? BACKLOG_POST_INTERVAL_MS : POST_INTERVAL_MS;
  if (millis() - lastPostMs < intervalMs) return;
  lastPostMs = millis();

  if (sampleQueueCount > 0) {
    const TelemetrySample& oldest = sampleQueue[sampleQueueTail];
    bool ok = postSample(oldest, true);
    if (ok) {
      sampleQueueTail = (sampleQueueTail + 1) % SAMPLE_QUEUE_CAP;
      sampleQueueCount--;
    }
    return;
  }

  TelemetrySample live;
  captureLatestSample(live);
  bool ok = postSample(live, false);
  if (!ok) enqueueSample(live);
}

int splitTabs(const String& line, String* out, int maxFields) {
  int count = 0;
  int start = 0;
  while (count < maxFields) {
    int idx = line.indexOf('\t', start);
    if (idx == -1) {
      out[count++] = line.substring(start);
      break;
    }
    out[count++] = line.substring(start, idx);
    start = idx + 1;
  }
  for (int i = 0; i < count; i++) out[i].trim();
  return count;
}

void parseCaHeader(const String& line) {
  int count = splitTabs(line, caFields, CA_MAX_FIELDS);
  caIdxAh = -1;
  caIdxV = -1;
  caIdxA = -1;
  caIdxS = -1;
  caIdxNm = -1;

  for (int i = 0; i < count; i++) {
    String c = caFields[i];
    if (c.equalsIgnoreCase("Ah")) caIdxAh = i;
    else if (c.equalsIgnoreCase("V")) caIdxV = i;
    else if (c.equalsIgnoreCase("A")) caIdxA = i;
    else if (c.equalsIgnoreCase("S")) caIdxS = i;
    else if (c.equalsIgnoreCase("Nm")) caIdxNm = i;
  }

  caHeaderParsed = (caIdxV >= 0 || caIdxA >= 0 || caIdxAh >= 0);
  Serial.print("CA header parsed=");
  Serial.println(caHeaderParsed ? "yes" : "no");
  if (caHeaderParsed) {
    Serial.print("Idx Ah/V/A/S/Nm = ");
    Serial.print(caIdxAh);
    Serial.print("/");
    Serial.print(caIdxV);
    Serial.print("/");
    Serial.print(caIdxA);
    Serial.print("/");
    Serial.print(caIdxS);
    Serial.print("/");
    Serial.println(caIdxNm);
  }
}

void parseCaDataRow(const String& line) {
  int count = splitTabs(line, caFields, CA_MAX_FIELDS);
  if (count <= 0) return;

  // Header may repeat in-stream; refresh mapping when it appears.
  if (caFields[0].equalsIgnoreCase("Ah")) {
    parseCaHeader(line);
    return;
  }

  int maxNeeded = caIdxAh;
  if (caIdxV > maxNeeded) maxNeeded = caIdxV;
  if (caIdxA > maxNeeded) maxNeeded = caIdxA;
  if (caIdxS > maxNeeded) maxNeeded = caIdxS;
  if (caIdxNm > maxNeeded) maxNeeded = caIdxNm;
  if (maxNeeded >= count) {
    data.rowsRejected++;
    return;
  }

  if (caIdxV >= 0) data.volts = caFields[caIdxV].toFloat();
  if (caIdxA >= 0) data.amps = caFields[caIdxA].toFloat();
  if (caIdxS >= 0) data.mph = caFields[caIdxS].toFloat();
  if (caIdxAh >= 0) data.ampHours = caFields[caIdxAh].toFloat();

  if (caIdxNm >= 0) {
    data.torque = caFields[caIdxNm].toFloat();
  } else {
    // Fallback estimate when CA stream does not include Nm.
    data.torque = data.amps * 0.54f;
  }

  data.lastUpdateMs = millis();
  data.rowsParsed++;
  queueCurrentSampleIfOffline();
}

void processTelemetryLine(const String& line) {
  String row = line;
  row.trim();
  if (row.length() == 0) return;

  if (row.indexOf('\t') >= 0) {
    if (!caHeaderParsed) {
      if (row.indexOf("Ah") >= 0 && row.indexOf("V") >= 0) {
        parseCaHeader(row);
      }
      return;
    }
    parseCaDataRow(row);
    return;
  }

  // Backward-compatible test format:
  // V=51.2,A=10.5,MPH=27.8,TQ=90.4,AH=5.1
  float v, a, m, tq, ah;
  int parsed = sscanf(row.c_str(), "V=%f,A=%f,MPH=%f,TQ=%f,AH=%f", &v, &a, &m, &tq, &ah);
  if (parsed == 5) {
    data.volts = v;
    data.amps = a;
    data.mph = m;
    data.torque = tq;
    data.ampHours = ah;
    data.lastUpdateMs = millis();
    data.rowsParsed++;
    queueCurrentSampleIfOffline();
  } else {
    data.rowsRejected++;
  }
}

void serviceTelemetryInput() {
  static String line;
  while (caSerial.available() > 0) {
    char c = (char)caSerial.read();
    if (c == '\n' || c == '\r') {
      if (line.length() > 0) {
        processTelemetryLine(line);
        line = "";
      }
    } else if (c >= 0x20 || c == '\t') {
      if (line.length() < 180) line += c;
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(180);
  Serial.println("EV telemetry boot");
  Serial.print("CA RX pin (3.3V only): ");
  Serial.println(CA_RX_PIN);
  Serial.print("CA baud: ");
  Serial.println(CA_BAUD);
  Serial.print("Offline queue cap: ");
  Serial.println(SAMPLE_QUEUE_CAP);
  Serial.print("Primary hotspot: ");
  Serial.println(hotspots[0].ssid);
  Serial.print("Secondary hotspot: ");
  Serial.println(hotspots[1].ssid);
  Serial.print("Upstream URL: ");
  Serial.println(SERVER_POST_URL);

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.persistent(false);
  secureClient.setInsecure();

  Wire.begin(OLED_SDA, OLED_SCL);
  caSerial.begin(CA_BAUD);
  oledReady = display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  if (oledReady) {
    drawHeader("EV Node Boot");
    display.setTextSize(1);
    display.setCursor(0, 20);
    display.println("OLED online");
    display.setCursor(0, 32);
    display.println("WiFi failover x2");
    display.display();
  } else {
    Serial.println("OLED init failed, continuing headless.");
  }

  startWifiAttempt(0);
  setupWeb();
}

void loop() {
  serviceWifi();
  serviceTelemetryInput();
  maybePostUpstream();
  web.handleClient();

  if (millis() - lastScreenSwitchMs >= SCREEN_HOLD_MS) {
    screenIndex = (screenIndex + 1) % TOTAL_SCREENS;
    lastScreenSwitchMs = millis();
  }
  renderScreen();
  delay(20);
}
