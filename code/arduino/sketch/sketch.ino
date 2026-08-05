/*
 * AI Home Energy Concierge - MCU firmware for the Arduino UNO Q (STM32U585).
 * MODULINO / QWIIC EDITION.
 *
 * Replaces the original breadboard build (PIR on D2, photoresistor on A0,
 * DHT22 on D4, servo on D9). All sensing now comes from Modulino nodes on the
 * Qwiic connector, which on this board is I2C4 == Wire1 (MCU pins PD12/PD13).
 * Verified empirically with code/arduino/scanner/scanner.ino.
 *
 * ---------------------------------------------------------------------------
 * WIRE CONTRACT - code/arduino/uno_q_publisher.py depends on this.
 *
 *   115200 baud, one compact JSON object per '\n'-terminated line, ~1 Hz.
 *   NO BANNER on Serial. The Linux-side parser expects JSON lines only.
 *
 *   MCU -> host (telemetry, 1 Hz):
 *     {"temp_c":21.9,"occ_src":"none","lux_src":"none","temp_src":"knob_sim",...}
 *
 * IMPORTANT - PARTIAL PAYLOADS ARE DELIBERATE.
 * This sketch emits ONLY the signals it genuinely has a source for. It does
 * NOT pad the line with placeholder occupancy/lux/humidity values.
 *
 * Why: the hub merges room state with {**prev, **payload} (hub/server.py
 * update_room), so a partial payload preserves whatever another source last
 * supplied. With only a Knob attached, the phone simulator owns
 * occupancy/lux/humidity and the MCU owns temp_c - and the two never fight.
 * If this sketch padded the missing keys with zeros at 1 Hz it would
 * continuously stomp on the phone's values.
 *
 * The original firmware had the opposite bug: with USE_DHT 0 it emitted
 * 23.5 C / 45.0 % indistinguishable on the wire from real readings, which
 * silently disabled rule R4 AND the R7 comfort guardrail. Hence the *_src
 * provenance keys below - a value always says where it came from.
 * ---------------------------------------------------------------------------
 *
 * HONESTY NOTE - READ BEFORE DEMOING.
 * The Modulino Knob is a DECLARED SIMULATED thermostat dial. It is not a
 * temperature sensor. Every line carries "temp_src":"knob_sim" to say so. If a
 * Modulino Thermo node is attached it is preferred automatically and temp_src
 * becomes "hs3003" - a real measurement. Never describe the knob value as a
 * measured temperature.
 *
 * SIGNAL OWNERSHIP (auto-detected at boot; missing nodes are simply omitted):
 *   temp_c    Thermo (real) -> else Knob (declared simulation)
 *   humidity  Thermo (real) -> else omitted
 *   lux       Light  (real) -> else Knob, if the Knob is not driving temp_c
 *   occupancy Distance (real) -> else Buttons override -> else omitted
 */

/* ------------------------------------------------------------------ *
 *  Feature flags. Compiling a node IN does not require it to be present -
 *  each is auto-detected via its begin() return value, and a missing node
 *  degrades one signal without ever preventing boot.
 * ------------------------------------------------------------------ */
#define USE_KNOB      1
#define USE_THERMO    1   // real temp + humidity, preferred over the knob
#define USE_DISTANCE  1   // ToF presence
#define USE_LIGHT     1   // real lux (LTR-381RGB); not in the 7-node kit
#define USE_BUTTONS   1   // manual demo scenario driving
#define USE_BUZZER    1   // audible feedback on button actions
#define USE_PIXELS    1   // local load indicator, if a Pixels node is attached
#define USE_MOVEMENT  0   // an IMU senses the NODE being jostled, not room
                          // occupancy. Deliberately off - see the README note.

/* Actuation lives on the network (Kasa smart bulb/plug, driven from the Linux
 * side), so the MCU does not need to receive commands. The reader is kept
 * behind this flag because it is unverified whether the UNO Q's Bridge/RPC
 * Serial path delivers host->MCU bytes at all. Flip to 1 only after testing. */
#define MCU_ACCEPTS_COMMANDS 0

#include <Arduino_Modulino.h>

/* ------------------------------------------------------------------ *
 *  Timing
 * ------------------------------------------------------------------ */
const unsigned long SAMPLE_MS = 1000;   // one telemetry line per second
const unsigned long POLL_MS   = 20;     // sensor/button poll cadence

/* Occupancy hold. The original 30 s window existed because a PIR emits PULSES
 * and the window reconstructed a continuous state. A ToF reading is already
 * continuous, so this now only bridges frames where the target is out of
 * range. Note uno_q_publisher.py's OccupancyFSM applies its own 30 s grace on
 * top, so the two compose - drop this to ~4000 for a snappier demo. */
const unsigned long OCC_WINDOW_MS = 30000;

/* ------------------------------------------------------------------ *
 *  Tuning
 * ------------------------------------------------------------------ */
/* Keep consistent with hub/rules.py: COMFORT_MAX_C 27.0, COMFORT_MIN_C 16.0 */
const float   TEMP_MIN_C       = 16.0;
const float   TEMP_MAX_C       = 32.0;
const int16_t KNOB_COUNTS_FULL = 100;

/* Knob press snaps between these, straddling R7's 27 C limit so the safety
 * gate is one click away on stage. */
const float PRESET_COMFY_C = 22.0;
const float PRESET_HOT_C   = 29.5;

/* Used when the Knob drives lux instead of temp (i.e. a Thermo is attached). */
const int LUX_MAX = 2000;

const float DIST_PRESENT_MM = 1000.0;   // nearer than this => someone is there
const float DIST_MIN_MM     = 20.0;     // below this is almost certainly noise

/* ------------------------------------------------------------------ *
 *  Nodes
 * ------------------------------------------------------------------ */
#if USE_KNOB
  ModulinoKnob     knob;      bool haveKnob = false;
#endif
#if USE_THERMO
  ModulinoThermo   thermo;    bool haveThermo = false;
#endif
#if USE_DISTANCE
  ModulinoDistance distance;  bool haveDistance = false;
#endif
#if USE_LIGHT
  ModulinoLight    light;     bool haveLight = false;
#endif
#if USE_BUTTONS
  ModulinoButtons  buttons;   bool haveButtons = false;
#endif
#if USE_BUZZER
  ModulinoBuzzer   buzzer;    bool haveBuzzer = false;
#endif
#if USE_PIXELS
  ModulinoPixels   pixels;    bool havePixels = false;
#endif

/* ------------------------------------------------------------------ *
 *  State
 * ------------------------------------------------------------------ */
unsigned long lastSample = 0, lastPoll = 0, lastPresence = 0;

bool  haveTemp = false, haveHum = false, haveLux = false, haveOcc = false;
float tempC = 0.0, humidity = 0.0;
int   luxVal = 0;
bool  occupied = false;
bool  occForce = false;          // button A pins occupancy true
float lastDistMm = -1.0;

const char *tempSrc = "none";
const char *humSrc  = "none";
const char *luxSrc  = "none";
const char *occSrc  = "none";

/* Local load mirror, only meaningful if a Pixels node is attached. The real
 * loads are Kasa devices switched from the Linux side. */
bool lightsOn = false, acOn = false;

#if MCU_ACCEPTS_COMMANDS
  char    cmdBuf[64];
  uint8_t cmdLen = 0;
#endif

/* ------------------------------------------------------------------ *
 *  Discovery. Safe to call repeatedly - only retries nodes not yet found, so
 *  hot-plugging works (button C) and a present node is never re-begun.
 * ------------------------------------------------------------------ */
void discoverNodes() {
#if USE_KNOB
  if (!haveKnob) {
    haveKnob = knob.begin();
    if (haveKnob) knob.set((int16_t)(((PRESET_COMFY_C - TEMP_MIN_C) * KNOB_COUNTS_FULL)
                                     / (TEMP_MAX_C - TEMP_MIN_C)));
  }
#endif
#if USE_THERMO
  if (!haveThermo)   haveThermo   = thermo.begin();
#endif
#if USE_DISTANCE
  if (!haveDistance) haveDistance = distance.begin();
#endif
#if USE_LIGHT
  if (!haveLight)    haveLight    = light.begin();
#endif
#if USE_BUTTONS
  if (!haveButtons)  haveButtons  = buttons.begin();
#endif
#if USE_BUZZER
  if (!haveBuzzer)   haveBuzzer   = buzzer.begin();
#endif
#if USE_PIXELS
  if (!havePixels)   havePixels   = pixels.begin();
#endif
}

/* One-letter map of live nodes, for field debugging. */
void printNodes() {
  Serial.print('"');
#if USE_KNOB
  if (haveKnob)     Serial.print('K');
#endif
#if USE_THERMO
  if (haveThermo)   Serial.print('T');
#endif
#if USE_DISTANCE
  if (haveDistance) Serial.print('D');
#endif
#if USE_LIGHT
  if (haveLight)    Serial.print('L');
#endif
#if USE_BUTTONS
  if (haveButtons)  Serial.print('B');
#endif
#if USE_BUZZER
  if (haveBuzzer)   Serial.print('Z');
#endif
#if USE_PIXELS
  if (havePixels)   Serial.print('P');
#endif
  Serial.print('"');
}

void chirp(int hz) {
#if USE_BUZZER
  if (haveBuzzer) buzzer.tone(hz, 90);   // fire-and-forget; the node times it
#endif
}

/* Is the Knob free to drive lux? Only when something real owns temp_c. */
bool knobDrivesLux() {
#if USE_THERMO
  return haveThermo;
#else
  return false;
#endif
}

/* ------------------------------------------------------------------ *
 *  Sensor polling
 * ------------------------------------------------------------------ */
void pollPresence(unsigned long now) {
  bool present = false;
  haveOcc = false;
  occSrc  = "none";

#if USE_DISTANCE
  if (haveDistance) {
    haveOcc = true;
    occSrc  = "tof";
    /* available() latches the newest valid result and returns false when the
       range status is bad (get() is NAN then). Poll it, never assume. */
    if (distance.available()) {
      float mm = distance.get();
      lastDistMm = mm;
      if (!isnan(mm) && mm > DIST_MIN_MM && mm < DIST_PRESENT_MM) present = true;
    } else {
      lastDistMm = -1.0;
    }
    if (present) lastPresence = now;
    occupied = (lastPresence != 0) && ((now - lastPresence) < OCC_WINDOW_MS);
  }
#endif

  if (occForce) {          // button A override wins, and says so
    occupied = true;
    haveOcc  = true;
    occSrc   = "button_override";
  }
}

void pollTempHumidity() {
  haveTemp = false;
  haveHum  = false;
  tempSrc  = "none";
  humSrc   = "none";

#if USE_THERMO
  if (haveThermo) {
    float t = thermo.getTemperature();
    float h = thermo.getHumidity();
    /* getTemperature()/getHumidity() return 0 - NOT NaN - when the node is
       uninitialised, so a plain isnan() check would ship 0.0 C as a real
       reading. Gate on begin() plus a sanity bound. */
    if (!isnan(t) && !isnan(h) && t > -40.0f && t < 125.0f && h >= 0.0f && h <= 100.0f) {
      tempC = t;    haveTemp = true;  tempSrc = "hs3003";
      humidity = h; haveHum  = true;  humSrc  = "hs3003";
      return;
    }
    tempSrc = "hs3003_bad_read";      // node present, reading rejected
    humSrc  = "hs3003_bad_read";
    return;
  }
#endif

#if USE_KNOB
  if (haveKnob && !knobDrivesLux()) {
    int16_t c = knob.get();
    /* Clamp and write back so the physical dial cannot drift away from the
       value being reported. */
    if (c < 0)                { c = 0;                knob.set(c); }
    if (c > KNOB_COUNTS_FULL) { c = KNOB_COUNTS_FULL; knob.set(c); }
    tempC = TEMP_MIN_C + ((TEMP_MAX_C - TEMP_MIN_C) * (float)c) / (float)KNOB_COUNTS_FULL;
    haveTemp = true;
    tempSrc  = "knob_sim";            // DECLARED SIMULATION. Never claim otherwise.
  }
#endif
  /* humidity stays absent without a Thermo - the phone simulator supplies it */
}

void pollLux() {
  haveLux = false;
  luxSrc  = "none";

#if USE_LIGHT
  if (haveLight && light.update()) {
    int v = light.getLux();
    if (v < 0) v = 0;
    if (v > LUX_MAX) v = LUX_MAX;
    luxVal  = v;
    haveLux = true;
    luxSrc  = "ltr381";
    return;
  }
#endif

#if USE_KNOB
  if (haveKnob && knobDrivesLux()) {
    int16_t c = knob.get();
    if (c < 0)                { c = 0;                knob.set(c); }
    if (c > KNOB_COUNTS_FULL) { c = KNOB_COUNTS_FULL; knob.set(c); }
    luxVal  = (int)(((long)c * LUX_MAX) / KNOB_COUNTS_FULL);
    haveLux = true;
    luxSrc  = "knob_sim";
    return;
  }
#endif
  /* lux stays absent - the phone simulator supplies it */
}

/* ------------------------------------------------------------------ *
 *  Buttons: A = occupancy override, B = clear presence, C = rescan bus.
 *
 *  ModulinoButtons::update() copies its I2C read buffer into last_status[]
 *  EVEN WHEN THE READ FAILED, so a flaky or absent node can emit phantom
 *  presses. Require the same level on two consecutive polls before acting.
 * ------------------------------------------------------------------ */
void pollButtons() {
#if USE_BUTTONS
  if (!haveButtons) return;

  static bool stable[3]  = { false, false, false };
  static bool pending[3] = { false, false, false };

  buttons.update();
  bool now3[3] = { buttons.isPressed(0) == HIGH,
                   buttons.isPressed(1) == HIGH,
                   buttons.isPressed(2) == HIGH };

  for (int i = 0; i < 3; i++) {
    if (now3[i] != stable[i] && now3[i] == pending[i]) {
      stable[i] = now3[i];
      if (stable[i]) {                       // confirmed rising edge
        if (i == 0)      { occForce = !occForce; chirp(occForce ? 880 : 440); }
        else if (i == 1) { lastPresence = 0; occForce = false; chirp(440); }
        else             { discoverNodes(); chirp(880); }
      }
    }
    pending[i] = now3[i];
  }

  buttons.setLeds(occForce, false, false);
#endif
}

void pollKnobButton() {
#if USE_KNOB
  if (!haveKnob || knobDrivesLux()) return;
  static bool prev = false;
  bool pressed = knob.isPressed();
  if (pressed && !prev) {
    /* Snap to whichever preset we are not already near - one click to cross
       R7's 27 C threshold during a demo. */
    float target = (tempC > (PRESET_COMFY_C + PRESET_HOT_C) / 2.0)
                     ? PRESET_COMFY_C : PRESET_HOT_C;
    knob.set((int16_t)(((target - TEMP_MIN_C) * KNOB_COUNTS_FULL)
                       / (TEMP_MAX_C - TEMP_MIN_C)));
    chirp(880);
  }
  prev = pressed;
#endif
}

/* ------------------------------------------------------------------ *
 *  Optional command path (compiled out by default - actuation is Kasa)
 * ------------------------------------------------------------------ */
#if MCU_ACCEPTS_COMMANDS
void renderPixels() {
#if USE_PIXELS
  if (!havePixels) return;
  for (int i = 0; i <= 3; i++) { if (lightsOn) pixels.set(i, YELLOW, 25); else pixels.clear(i); }
  for (int i = 4; i <= 7; i++) { if (acOn)     pixels.set(i, CYAN,   25); else pixels.clear(i); }
  pixels.show();
#endif
}

void handleCommand(char *line) {
  char *verb = strtok(line, " \t");
  if (verb == NULL || strcmp(verb, "CMD") != 0) return;
  char *load  = strtok(NULL, " \t");
  char *state = strtok(NULL, " \t");
  if (load == NULL || state == NULL) return;

  bool turnOn = (strcmp(state, "on") == 0);
  bool known  = true;
  if      (strcmp(load, "lights") == 0) lightsOn = turnOn;
  else if (strcmp(load, "ac")     == 0) acOn     = turnOn;
  else                                  known    = false;

  renderPixels();
  chirp(known ? (turnOn ? 880 : 440) : 196);

  Serial.print(F("{\"ack\":\""));      Serial.print(load);
  Serial.print(F("\",\"state\":\""));  Serial.print(turnOn ? F("on") : F("off"));
  Serial.print(F("\",\"ok\":"));       Serial.print(known ? F("true") : F("false"));
  Serial.println(F(",\"via\":\"pixels\"}"));
}

void pollCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) { cmdBuf[cmdLen] = '\0'; handleCommand(cmdBuf); cmdLen = 0; }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdLen = 0;                       // overlong line: drop, never overflow
    }
  }
}
#endif

/* ------------------------------------------------------------------ *
 *  setup / loop
 * ------------------------------------------------------------------ */
void setup() {
  Serial.begin(115200);

  /* ARDUINO_UNO_Q makes this default to Wire1 (the Qwiic bus), but pass it
     explicitly so the sketch stays correct on a platform build where that
     define is missing. */
  Modulino.begin(Wire1);
  discoverNodes();

  /* Prime the first readings so the very first telemetry line is meaningful. */
  pollTempHumidity();
  pollLux();

  /* NO BANNER. The Linux parser expects JSON lines only. Node presence is
     reported inside each telemetry line via "nodes". */
}

void loop() {
  unsigned long now = millis();

#if MCU_ACCEPTS_COMMANDS
  pollCommands();
#endif

  /* Modulino reads are real I2C transactions at 100 kHz, not free digitalReads.
     Polling every loop pass (as the PIR build did) would saturate the bus and
     starve Serial. 20 ms beats the ToF's own 20 ms budget and any human press. */
  if (now - lastPoll >= POLL_MS) {
    lastPoll = now;
    pollPresence(now);
    pollButtons();
    pollKnobButton();
  }

  if (now - lastSample < SAMPLE_MS) return;   // non-blocking; no delay() anywhere
  lastSample = now;

  pollTempHumidity();
  pollLux();

  /* One compact JSON object per line. Only signals with a real source are
     emitted - see the PARTIAL PAYLOADS note at the top of this file. */
  Serial.print(F("{"));

  bool first = true;
  if (haveOcc) {
    Serial.print(F("\"occupancy\":")); Serial.print(occupied ? F("true") : F("false"));
    first = false;
  }
  if (haveLux) {
    if (!first) Serial.print(F(","));
    Serial.print(F("\"lux\":")); Serial.print(luxVal);
    first = false;
  }
  if (haveTemp) {
    if (!first) Serial.print(F(","));
    Serial.print(F("\"temp_c\":")); Serial.print(tempC, 1);
    first = false;
  }
  if (haveHum) {
    if (!first) Serial.print(F(","));
    Serial.print(F("\"humidity\":")); Serial.print(humidity, 1);
    first = false;
  }

  /* Provenance always present, so a consumer can tell measurement from
     simulation without guessing. */
  if (!first) Serial.print(F(","));
  Serial.print(F("\"occ_src\":\""));     Serial.print(occSrc);
  Serial.print(F("\",\"lux_src\":\""));  Serial.print(luxSrc);
  Serial.print(F("\",\"temp_src\":\"")); Serial.print(tempSrc);
  Serial.print(F("\",\"hum_src\":\""));  Serial.print(humSrc);
  Serial.print(F("\",\"dist_mm\":"));    Serial.print(lastDistMm, 0);
  Serial.print(F(",\"nodes\":"));        printNodes();
  Serial.println(F("}"));
}
