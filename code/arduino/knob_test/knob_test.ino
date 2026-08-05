/*
 * STEP ZERO (b) - Modulino Knob smoke test for the Arduino UNO Q.
 *
 * Flash this after scanner.ino confirms a Knob on Wire1. It proves the whole
 * chain works: UNO Q sketch -> Wire1 -> Qwiic -> Modulino Knob.
 *
 * It also previews the real demo mechanic: in the final firmware the Knob is a
 * DECLARED simulated thermostat dial driving temp_c, which is the input to
 * rule R7 - the comfort guardrail that REFUSES to switch off the A/C above
 * 27 C. So turning this knob past 27.0 on stage is what makes the system
 * decline its own advice.
 *
 * Banner and human-readable output are fine HERE (diagnostic sketch). The real
 * sketch.ino must stay silent apart from JSON, because uno_q_publisher.py
 * parses its output as JSON lines only.
 *
 * Serial monitor: 115200 baud.
 * Expect: one line per 250 ms; turning the knob moves both numbers; pressing
 * the knob snaps between 22.0 C and 29.5 C (either side of R7's 27 C limit).
 */

#include <Arduino_Modulino.h>
/* Library >= 0.7 ships Arduino_Modulino.h. Older 0.6.x ships only Modulino.h -
   if the include above fails to resolve, swap it for:  #include <Modulino.h> */

ModulinoKnob knob;
bool haveKnob = false;

/* Must match rules.py: COMFORT_MAX_C = 27.0, COMFORT_MIN_C = 16.0 */
const float TEMP_MIN_C   = 16.0;
const float TEMP_MAX_C   = 32.0;
const float COMFORT_MAX_C = 27.0;

/* Encoder detents spanning the full temperature range. */
const int16_t KNOB_COUNTS_FULL = 100;

/* Knob press snaps between these two, deliberately straddling COMFORT_MAX_C so
   the R7 refusal is one click away during a demo. */
const float PRESET_COMFY_C = 22.0;
const float PRESET_HOT_C   = 29.5;

float countsToTempC(int16_t counts) {
  if (counts < 0)                counts = 0;
  if (counts > KNOB_COUNTS_FULL) counts = KNOB_COUNTS_FULL;
  return TEMP_MIN_C + ((TEMP_MAX_C - TEMP_MIN_C) * (float)counts) / (float)KNOB_COUNTS_FULL;
}

int16_t tempCToCounts(float c) {
  if (c < TEMP_MIN_C) c = TEMP_MIN_C;
  if (c > TEMP_MAX_C) c = TEMP_MAX_C;
  return (int16_t)(((c - TEMP_MIN_C) * (float)KNOB_COUNTS_FULL) / (TEMP_MAX_C - TEMP_MIN_C));
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && (millis() - t0) < 5000) { }

  Serial.println();
  Serial.println("=========================================");
  Serial.println(" Modulino KNOB smoke test");
  Serial.println("=========================================");

  /* ARDUINO_UNO_Q makes this default to Wire1 (the Qwiic bus), but pass it
     explicitly so the sketch is correct even on an older platform build where
     that define is missing. */
  Modulino.begin(Wire1);

  haveKnob = knob.begin();

  if (!haveKnob) {
    Serial.println("KNOB NOT FOUND on Wire1.");
    Serial.println("  - Re-run scanner.ino; expect 0x3A or 0x3B on Wire1.");
    Serial.println("  - Check the Qwiic cable is seated at BOTH ends.");
    Serial.println("  - This sketch will keep retrying every 2 s.");
  } else {
    knob.set(tempCToCounts(PRESET_COMFY_C));   // start somewhere sensible
    Serial.println("Knob found. Turn it. Press it to snap 22.0 <-> 29.5 C.");
    Serial.print("R7 comfort limit is ");
    Serial.print(COMFORT_MAX_C, 1);
    Serial.println(" C - above that the system refuses to switch off the A/C.");
    Serial.println();
  }
}

void loop() {
  if (!haveKnob) {
    haveKnob = knob.begin();          // allow hot-plugging the node
    if (haveKnob) {
      knob.set(tempCToCounts(PRESET_COMFY_C));
      Serial.println("Knob appeared - carry on.");
    } else {
      delay(2000);
      return;
    }
  }

  int16_t counts = knob.get();

  /* Clamp the encoder and write the clamp back, so the physical dial cannot
     run away from the value being reported. */
  if (counts < 0)                { counts = 0;                knob.set(counts); }
  if (counts > KNOB_COUNTS_FULL) { counts = KNOB_COUNTS_FULL; knob.set(counts); }

  /* Knob press: snap to whichever preset we are NOT currently near. */
  static bool prevPressed = false;
  bool pressed = knob.isPressed();
  if (pressed && !prevPressed) {
    float now = countsToTempC(counts);
    float target = (now > (PRESET_COMFY_C + PRESET_HOT_C) / 2.0)
                     ? PRESET_COMFY_C : PRESET_HOT_C;
    counts = tempCToCounts(target);
    knob.set(counts);
    Serial.print(">>> snapped to ");
    Serial.print(target, 1);
    Serial.println(" C");
  }
  prevPressed = pressed;

  float tempC = countsToTempC(counts);

  Serial.print("counts=");
  Serial.print(counts);
  Serial.print("   temp_c=");
  Serial.print(tempC, 1);
  Serial.print(" C   ");
  Serial.println(tempC > COMFORT_MAX_C ? "[ABOVE R7 LIMIT - A/C off would be REFUSED]"
                                       : "[within comfort range - A/C off allowed]");

  delay(250);
}
