"""
smart_lock.py - A basic smart lock: unlock with a known RFID card or the right PIN on the keypad.

What it does:
  - Waits for either input:
      RFID card : if its UID is in AUTHORIZED_CARDS, the lock opens. Unknown cards are refused, and their UID is
                  printed so you can add it to the list.
      Keypad    : type the PIN and press # to submit. * clears what you've typed so far.
  - Opening means switching LOCK_GPIO on for UNLOCK_SECONDS (wire a relay module, an electric strike or simply an
    LED to it), then switching it off again.
  - After MAX_WRONG_PINS wrong PINs in a row, the keypad is ignored for LOCKOUT_SECONDS.
  - Every event goes through report_event(). For now it only prints, but that's where the wireless part of the
    project would go: send the event over Wi-Fi (MQTT, HTTP), Bluetooth, LoRa, etc.

Files: rc522.py (RFID reader driver) and keypad.py (keypad driver) must be in the same folder.
Run:   python3 smart_lock.py        (Ctrl+C to stop)
"""

import time

from gpiozero import DigitalOutputDevice

from keypad import Keypad
from rc522 import RC522


# ---------------- Settings ----------------
AUTHORIZED_CARDS = {
    # UID (as printed by rc522.py)  : who it belongs to
    "04A1B2C3": "Example card",
}
PIN_CODE = "1234"

LOCK_GPIO = 17          # Pi pin 11. HIGH = unlocked. Use a relay module or transistor for a real lock, never the pin directly
UNLOCK_SECONDS = 5      # How long the lock stays open
MAX_WRONG_PINS = 3      # Wrong PINs in a row before the keypad locks out
LOCKOUT_SECONDS = 30
CARD_GONE_POLLS = 3     # A card counts as removed after this many empty reads in a row (see read_uid in rc522.py)
LOOP_DELAY = 0.05       # 50 ms between checks: fast enough to feel instant, light on the CPU


def report_event(kind, detail=""):
    """Record one event. Replace or extend this to send events wirelessly, for example:
       mqtt_client.publish("smartlock/events", f"{kind}:{detail}")"""
    stamp = time.strftime("%H:%M:%S")
    print(f"[{stamp}] {kind} {detail}".rstrip())


class SmartLock:
    def __init__(self):
        self.reader = RC522()
        self.keypad = Keypad()
        self.lock = DigitalOutputDevice(LOCK_GPIO, initial_value=False)  # Start locked

        self.typed = ""              # Digits typed so far
        self.wrong_pins = 0          # Wrong PINs in a row
        self.lockout_until = 0.0     # time.monotonic() value when the keypad works again
        self.card_present = None     # UID of the card on the reader right now, so one tap counts once
        self.card_misses = 0

    # ---------- Lock output ----------

    def unlock(self, reason):
        report_event("UNLOCKED", reason)
        self.lock.on()
        time.sleep(UNLOCK_SECONDS)   # Simple version: inputs are not checked while the door is open
        self.lock.off()
        report_event("LOCKED")

    # ---------- RFID ----------

    def check_card(self):
        uid = self.reader.read_uid()
        if uid is None:
            # A card left on the reader doesn't answer every time, so wait for several misses before
            # deciding it's gone. Only then can the same card trigger the lock again
            self.card_misses += 1
            if self.card_misses >= CARD_GONE_POLLS:
                self.card_present = None
            return

        self.card_misses = 0
        if uid == self.card_present:
            return                   # Same card still on the reader: already handled
        self.card_present = uid

        if uid in AUTHORIZED_CARDS:
            self.unlock(f"card {uid} ({AUTHORIZED_CARDS[uid]})")
        else:
            report_event("CARD_REFUSED", uid)

    # ---------- Keypad ----------

    def check_keypad(self):
        key = self.keypad.scan()
        if key is None:
            return

        if time.monotonic() < self.lockout_until:
            report_event("KEYPAD_LOCKED_OUT")
            return

        if key == "*":               # Clear
            self.typed = ""
            report_event("PIN_CLEARED")
        elif key == "#":             # Submit
            self.submit_pin()
        elif key.isdigit():
            self.typed += key
            print("PIN:", "*" * len(self.typed))  # Show progress without showing the digits
        # A, B, C and D do nothing here: free for your own features (doorbell, change PIN, ...)

    def submit_pin(self):
        entered, self.typed = self.typed, ""
        if entered == PIN_CODE:
            self.wrong_pins = 0
            self.unlock("PIN")
            return

        self.wrong_pins += 1
        report_event("PIN_WRONG", f"{self.wrong_pins}/{MAX_WRONG_PINS}")
        if self.wrong_pins >= MAX_WRONG_PINS:
            self.wrong_pins = 0
            self.lockout_until = time.monotonic() + LOCKOUT_SECONDS
            report_event("KEYPAD_LOCKOUT", f"{LOCKOUT_SECONDS}s")

    # ---------- Main loop ----------

    def run(self):
        report_event("READY", f"RC522 version 0x{self.reader.version:02X}")
        while True:
            self.check_card()
            self.check_keypad()
            time.sleep(LOOP_DELAY)

    def close(self):
        self.lock.off()              # Leave the door locked when the program stops
        self.lock.close()
        self.reader.close()
        self.keypad.close()


if __name__ == "__main__":
    smart_lock = SmartLock()
    try:
        smart_lock.run()
    except KeyboardInterrupt:
        pass
    finally:
        smart_lock.close()
