/*
 * STEP ZERO - Arduino UNO Q Qwiic / I2C bus scanner.
 *
 * Flash this BEFORE any application code. It answers three questions at once:
 *   1. Can the STM32 sketch reach the Qwiic connector at all?
 *   2. Which Modulino nodes are actually attached, and at what addresses?
 *   3. Is ARDUINO_UNO_Q defined by this board platform version?
 *
 * WHY BOTH BUSES: Arduino's UNO Q user manual puts the Qwiic connector on the
 * secondary I2C bus (I2C4 == Wire1, MCU pins PD12/PD13), NOT the default Wire.
 * The Arduino_Modulino library agrees - it special-cases ARDUINO_UNO_Q to
 * default to Wire1. We scan both anyway, so a surprise is visible instead of
 * silent.
 *
 * A banner on Serial is fine HERE. This is a diagnostic, not the telemetry
 * sketch - the real sketch.ino must stay silent because the Linux-side parser
 * (uno_q_publisher.py) expects JSON lines only.
 *
 * EXPECTED: at least one address listed under "Wire1 (Qwiic / I2C4)".
 * If BOTH buses come back empty, stop - check the Qwiic cable seating and that
 * the node has power - before writing any application code.
 *
 * Serial monitor: 115200 baud.
 */

#include <Wire.h>

/*
 * Known Modulino 7-bit addresses.
 *
 * NOTE ON THE 8-BIT/7-BIT SPLIT: the Arduino_Modulino library stores 8-bit
 * addresses in its internal match[] tables and halves them before use
 * (Module::scan() does beginTransmission(addr / 2)). The values below are
 * therefore match[] >> 1, which is what Wire actually puts on the bus.
 */
struct KnownNode {
  uint8_t     addr7;
  const char *name;
};

static const KnownNode KNOWN[] = {
  { 0x1E, "Modulino BUZZER    (lib match 0x3C)" },
  { 0x29, "Modulino DISTANCE  (VL53L4CD/ED)" },
  { 0x36, "Modulino PIXELS    (lib match 0x6C)" },
  { 0x3A, "Modulino KNOB      (lib match 0x74)" },
  { 0x3B, "Modulino KNOB alt  (lib match 0x76)" },
  { 0x3E, "Modulino BUTTONS   (lib match 0x7C)" },
  { 0x44, "Modulino THERMO    (HS3003)      [addr unverified]" },
  { 0x53, "Modulino LIGHT     (LTR-381RGB)  [addr unverified]" },
  { 0x6A, "Modulino MOVEMENT  (LSM6DSOX)" },
  { 0x6B, "Modulino MOVEMENT  (LSM6DSOX, SDO high)" },
};
static const size_t KNOWN_N = sizeof(KNOWN) / sizeof(KNOWN[0]);

const char *identify(uint8_t addr7) {
  for (size_t i = 0; i < KNOWN_N; i++) {
    if (KNOWN[i].addr7 == addr7) return KNOWN[i].name;
  }
  return "unknown device";
}

void scanBus(TwoWire &bus, const char *label) {
  Serial.print("--- ");
  Serial.print(label);
  Serial.println(" ---");

  int found = 0;
  for (uint8_t addr = 0x08; addr <= 0x77; addr++) {   // skip the reserved ranges
    bus.beginTransmission(addr);
    if (bus.endTransmission() == 0) {
      found++;
      Serial.print("  0x");
      if (addr < 0x10) Serial.print('0');
      Serial.print(addr, HEX);
      Serial.print("  ");
      Serial.println(identify(addr));
    }
  }

  if (found == 0) Serial.println("  (nothing responded)");
  Serial.print("  total: ");
  Serial.println(found);
}

void setup() {
  Serial.begin(115200);

  // Wait briefly for a monitor, but never block forever - the board must still
  // run when nothing is attached.
  unsigned long t0 = millis();
  while (!Serial && (millis() - t0) < 5000) { }

  Serial.println();
  Serial.println("=========================================");
  Serial.println(" UNO Q I2C scanner - Modulino bring-up");
  Serial.println("=========================================");

#if defined(ARDUINO_UNO_Q)
  Serial.println("board define ARDUINO_UNO_Q: PRESENT");
#else
  Serial.println("board define ARDUINO_UNO_Q: *** ABSENT ***");
  Serial.println("  -> Arduino_Modulino would default to Wire, NOT Wire1.");
  Serial.println("  -> Update the UNO Q board platform (need >= 0.55.0),");
  Serial.println("     or always pass the bus explicitly: Modulino.begin(Wire1);");
#endif

  Wire.begin();
  Wire1.begin();
  Wire.setClock(100000);    // match what ModulinoClass::begin() uses
  Wire1.setClock(100000);
}

void loop() {
  Serial.println();
  scanBus(Wire1, "Wire1  (Qwiic / I2C4, MCU pins PD12/PD13)");
  scanBus(Wire,  "Wire   (D20/D21 headers, MCU pins PB10/PB11)");
  delay(3000);
}
