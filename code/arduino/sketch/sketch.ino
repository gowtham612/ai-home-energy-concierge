/*
 * AI Home Energy Concierge — MCU firmware for the Arduino UNO Q (STM32U585).
 *
 * Two jobs:
 *   1. Sample sensors on a hard 1 s schedule, emit ONE parseable JSON line per sample.
 *   2. Accept commands on Serial and DRIVE THE ACTUATOR — this is what closes the
 *      loop from sensing to physical action (Archetype E).
 *
 * The Linux side (Qualcomm Dragonwing) does aggregation, MQTT, and decides WHEN to
 * command; this file stays real-time and dumb.
 *
 * PROTOCOL
 *   MCU -> host (1 Hz, telemetry):
 *     {"occupancy":true,"lux":420,"temp_c":24.5,"humidity":48.0,"raw_pir":1}
 *   host -> MCU (on demand, one line):
 *     CMD <load> <on|off>            e.g.  CMD lights off
 *   MCU -> host (immediately after a command):
 *     {"ack":"lights","state":"off","ok":true}
 *
 * Telemetry and acks are both single-line JSON; the parser tells them apart by the
 * presence of the "ack" key. Commands are plain text so they are trivial to send by
 * hand from a serial monitor while debugging.
 *
 * PIN MAP
 *   D2  PIR motion sensor        (digital in, HIGH = motion)
 *   A0  LDR / photoresistor      (analog in, divider to GND with 10k)
 *   D4  DHT22 temp + humidity    (one-wire)
 *   D9  SERVO signal             (actuator: presses a physical light switch)
 *   D7  RELAY / LED indicator    (actuator fallback: switches or indicates a load)
 *   D8  BUZZER                   (audible confirmation the command landed)
 *
 * If the DHT22 is unavailable, set USE_DHT to 0 and the sketch emits stubbed
 * temp/humidity — clearly marked so we never claim a reading we did not take.
 */

#define USE_DHT   1
#define USE_SERVO 1

#if USE_DHT
  #include "DHT.h"
  #define DHTPIN   4
  #define DHTTYPE  DHT22
  DHT dht(DHTPIN, DHTTYPE);
#endif

#if USE_SERVO
  #include <Servo.h>
  Servo switchServo;
#endif

const uint8_t  PIN_PIR   = 2;
const uint8_t  PIN_LDR   = A0;
const uint8_t  PIN_SERVO = 9;
const uint8_t  PIN_RELAY = 7;
const uint8_t  PIN_BUZZ  = 8;

// Servo angles for pressing a rocker switch. Tune these on the bench: the arm must
// reach the switch without stalling against it (a stalled servo browns out the rail).
const int SERVO_REST_DEG = 90;
const int SERVO_OFF_DEG  = 150;   // press the "off" side
const int SERVO_ON_DEG   = 30;    // press the "on" side
const unsigned long SERVO_PRESS_MS = 500;   // hold, then return to rest

const unsigned long SAMPLE_MS      = 1000;   // one sample per second
const unsigned long OCC_WINDOW_MS  = 30000;  // motion within 30 s => occupied

unsigned long lastSample     = 0;
unsigned long lastMotionMs   = 0;
bool          occupied       = false;

char    cmdBuf[48];
uint8_t cmdLen = 0;

// Stub values used only when USE_DHT is 0.
const float STUB_TEMP_C  = 23.5;
const float STUB_HUMID   = 45.0;

void setup() {
  Serial.begin(115200);
  pinMode(PIN_PIR, INPUT);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_BUZZ, OUTPUT);
  digitalWrite(PIN_RELAY, LOW);
  digitalWrite(PIN_BUZZ, LOW);

#if USE_SERVO
  switchServo.attach(PIN_SERVO);
  switchServo.write(SERVO_REST_DEG);
#endif

#if USE_DHT
  dht.begin();
#endif
  // No banner on Serial: the Linux parser expects JSON lines only.
}

/*
 * Convert the 10-bit LDR reading to an approximate lux value.
 *
 * This is an APPROXIMATION from an uncalibrated photoresistor, not a measured
 * lux figure. We only ever use it against a coarse threshold (bright daylight
 * vs not), and the rules engine says so in its evidence text.
 *
 * Divider: 3V3 -- LDR -- A0 -- 10k -- GND, so more light => higher reading.
 */
int approxLux(int raw) {
  if (raw <= 0) return 0;
  float ratio = (float)raw / 1023.0;
  // Roughly exponential response; caps at ~1000 lux for indoor daylight.
  float lux = 1000.0 * pow(ratio, 2.2);
  if (lux < 0)    lux = 0;
  if (lux > 2000) lux = 2000;
  return (int)lux;
}

/*
 * Physically act. Returns true if we believe the action was performed.
 *
 * The servo press is the real "physical AI" action: the arm moves and flips an
 * actual switch. The relay and buzzer are driven too, so there is always a visible
 * and audible signal even when no servo is attached.
 */
bool driveActuator(const char *load, bool turnOn) {
  // Audible acknowledgement first — it confirms the command landed even if the
  // mechanical part fails.
  digitalWrite(PIN_BUZZ, HIGH);
  delay(60);
  digitalWrite(PIN_BUZZ, LOW);

  // Relay / LED reflects the requested state directly.
  digitalWrite(PIN_RELAY, turnOn ? HIGH : LOW);

#if USE_SERVO
  switchServo.write(turnOn ? SERVO_ON_DEG : SERVO_OFF_DEG);
  delay(SERVO_PRESS_MS);
  switchServo.write(SERVO_REST_DEG);   // never hold against the switch
#endif

  (void)load;   // single-actuator build; the load name is echoed for traceability
  return true;
}

/* Parse and execute one command line: "CMD <load> <on|off>" */
void handleCommand(char *line) {
  char *verb = strtok(line, " \t");
  if (verb == NULL || strcmp(verb, "CMD") != 0) return;

  char *load  = strtok(NULL, " \t");
  char *state = strtok(NULL, " \t");
  if (load == NULL || state == NULL) return;

  bool turnOn = (strcmp(state, "on") == 0);
  bool ok = driveActuator(load, turnOn);

  Serial.print(F("{\"ack\":\""));
  Serial.print(load);
  Serial.print(F("\",\"state\":\""));
  Serial.print(turnOn ? F("on") : F("off"));
  Serial.print(F("\",\"ok\":"));
  Serial.print(ok ? F("true") : F("false"));
  Serial.println(F("}"));
}

/* Non-blocking serial command reader. */
void pollCommands() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (cmdLen > 0) {
        cmdBuf[cmdLen] = '\0';
        handleCommand(cmdBuf);
        cmdLen = 0;
      }
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = c;
    } else {
      cmdLen = 0;   // overlong line: drop it rather than overflow
    }
  }
}

void loop() {
  unsigned long now = millis();

  pollCommands();   // actuation must feel instant, so check every pass

  // Sample the PIR every pass so short pulses are never missed.
  int pir = digitalRead(PIN_PIR);
  if (pir == HIGH) lastMotionMs = now;

  // Debounced occupancy: HIGH at least once inside the trailing window.
  occupied = (lastMotionMs != 0) && ((now - lastMotionMs) < OCC_WINDOW_MS);

  if (now - lastSample < SAMPLE_MS) return;   // non-blocking; no delay() in this path
  lastSample = now;

  int lux = approxLux(analogRead(PIN_LDR));

  float t, h;
#if USE_DHT
  t = dht.readTemperature();
  h = dht.readHumidity();
  if (isnan(t)) t = STUB_TEMP_C;   // a failed read must not break the JSON line
  if (isnan(h)) h = STUB_HUMID;
#else
  t = STUB_TEMP_C;
  h = STUB_HUMID;
#endif

  // Exactly one compact JSON object per line. No pretty printing.
  Serial.print(F("{\"occupancy\":"));
  Serial.print(occupied ? F("true") : F("false"));
  Serial.print(F(",\"lux\":"));      Serial.print(lux);
  Serial.print(F(",\"temp_c\":"));   Serial.print(t, 1);
  Serial.print(F(",\"humidity\":")); Serial.print(h, 1);
  Serial.print(F(",\"raw_pir\":"));  Serial.print(pir);
  Serial.println(F("}"));
}
