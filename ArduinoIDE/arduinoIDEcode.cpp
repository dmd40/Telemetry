//------ code for the arduino IDE running 1.18.18

#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
#define SCREEN_ADDRESS 0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// -------------------- Telemetry state --------------------
struct TelemetryData {
  float volts;
  float amps;
  float mph;
  float torque;
  float ampHours;
};

TelemetryData data = {48.7f, 12.3f, 31.5f, 87.2f, 4.8f};

// Set this from your radio/ESP-NOW link code when a packet arrives.
volatile unsigned long lastPacketMs = 0;
const unsigned long CONNECTION_TIMEOUT_MS = 3000;

bool isConnected() {
  return (millis() - lastPacketMs) <= CONNECTION_TIMEOUT_MS;
}

// -------------------- Screen rotation --------------------
const unsigned long SCREEN_HOLD_MS = 5000;
unsigned long lastScreenSwitchMs = 0;
uint8_t screenIndex = 0;

const uint8_t TOTAL_SCREENS = 5;

// -------------------- Helpers --------------------
void drawHeader(bool connected) {
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.print("Status: ");
  display.print(connected ? "CONNECTED" : "NOT CONNECTED");
  display.drawLine(0, 10, SCREEN_WIDTH - 1, 10, SSD1306_WHITE);
}

void drawMetric(const char* label, float value, const char* unit) {
  display.setTextSize(2);
  display.setCursor(0, 22);
  display.print(label);

  display.setTextSize(2);
  display.setCursor(0, 44);
  display.print(value, 1);
  display.print(" ");
  display.print(unit);
}

void renderScreen() {
  bool connected = isConnected();

  display.clearDisplay();
  drawHeader(connected);

  switch (screenIndex) {
    case 0:
      drawMetric("V", data.volts, "V");
      break;
    case 1:
      drawMetric("A", data.amps, "A");
      break;
    case 2:
      drawMetric("mph", data.mph, "mph");
      break;
    case 3:
      drawMetric("torque", data.torque, "Nm");
      break;
    case 4:
      drawMetric("Ah", data.ampHours, "Ah");
      break;
  }

  display.display();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  if (!display.begin(SSD1306_SWITCHCAPVCC, SCREEN_ADDRESS)) {
    Serial.println(F("SSD1306 allocation failed"));
    for (;;) {
    }
  }

  display.clearDisplay();
  display.display();

  // Demo default: mark as connected at boot.
  // remove when connected to cycle ananalyst
  lastPacketMs = millis();
}

void loop() {
  // Optional serial demo input:
  // Send: V=51.2,A=10.5,MPH=27.8,TQ=90.4,AH=5.1
  // Also changes the rest time change
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();

    float v, a, m, tq, ah;
    int parsed = sscanf(line.c_str(), "V=%f,A=%f,MPH=%f,TQ=%f,AH=%f", &v, &a, &m, &tq, &ah);
    if (parsed == 5) {
      data.volts = v;
      data.amps = a;
      data.mph = m;
      data.torque = tq;
      data.ampHours = ah;
      lastPacketMs = millis();
    }
  }

  // Cycle through screens every 5 seconds.
  if (millis() - lastScreenSwitchMs >= SCREEN_HOLD_MS) {
    screenIndex = (screenIndex + 1) % TOTAL_SCREENS;
    lastScreenSwitchMs = millis();
  }

  renderScreen();
  delay(50);
}
