"""
keypad.py - 4x4 matrix keypad driver for the Raspberry Pi.

How a matrix keypad works:
  16 keys would normally need 16 wires. A matrix keypad uses only 8: 4 row wires and 4 column wires.
  Each key sits where one row crosses one column; pressing it joins that row wire to that column wire.

  To find which key is down, the Pi "scans" the rows:
    - The column pins are inputs with pull-up resistors, so they read HIGH when nothing is pressed.
    - Every row pin is an output, normally HIGH.
    - One row at a time is driven LOW. If a key in that row is pressed, it connects its column to the LOW row,
      and that column reads LOW. Row number + column number = exactly one key.

        Col1 Col2 Col3 Col4
  Row1   1    2    3    A
  Row2   4    5    6    B
  Row3   7    8    9    C
  Row4   *    0    #    D

  Debouncing: a key's metal contacts bounce for a few milliseconds when pressed, which can look like several
  presses. The scanner waits 20 ms and checks again before it accepts a key, and then waits for the key to be
  released, so holding a key down counts as one press.

Wiring (looking at the keypad's front, the 8 pins are usually R1 R2 R3 R4 C1 C2 C3 C4 from left to right):
  Keypad pin   Pi pin   Pi GPIO
  R1           29       GPIO5
  R2           31       GPIO6
  R3           33       GPIO13
  R4           35       GPIO19
  C1           32       GPIO12
  C2           36       GPIO16
  C3           38       GPIO20
  C4           40       GPIO21
No resistors are needed: the Pi's internal pull-ups are used on the column pins.

Keypads are not all wired the same way. Run this file on its own (python3 keypad.py) and press each key:
if the printed characters are wrong, swap pins or edit KEYMAP below to match what you see.
(The 10-pin keypad on our IoT dashboard needed a different map; see the note under KEYMAP.)
"""

import time

from gpiozero import DigitalInputDevice, DigitalOutputDevice


# KEYMAP[row][column]: the character printed on the key where that row and column cross
KEYMAP = [
    ["1", "2", "3", "A"],
    ["4", "5", "6", "B"],
    ["7", "8", "9", "C"],
    ["*", "0", "#", "D"],
]
# The 10-pin keypad used on our ESP32 dashboard has its pins in a different order, and the dashboard firmware uses
# the map below. If you use that keypad, try this map first, then check every key with the test at the bottom:
#   [["1", "*", "4", "7"], ["2", "0", "5", "8"], ["3", "#", "6", "9"], ["A", "D", "B", "C"]]

ROW_GPIOS = [5, 6, 13, 19]
COL_GPIOS = [12, 16, 20, 21]

DEBOUNCE_S = 0.02  # 20 ms: long enough for the contacts to stop bouncing


class Keypad:
    def __init__(self, row_gpios=ROW_GPIOS, col_gpios=COL_GPIOS, keymap=KEYMAP):
        # Rows are outputs and start HIGH (idle). Driving one LOW selects that row for scanning
        self._rows = [DigitalOutputDevice(pin, initial_value=True) for pin in row_gpios]
        # Columns are inputs with pull-ups. With pull_up=True, gpiozero counts LOW as "active",
        # so col.is_active is True when a pressed key pulls the column LOW
        self._cols = [DigitalInputDevice(pin, pull_up=True) for pin in col_gpios]
        self._keymap = keymap

    def scan(self):
        """Check every key once. Returns the pressed key's character (after it is released), or None.
        Call it often (every 10-50 ms) from your main loop."""
        for r, row in enumerate(self._rows):
            row.off()             # Drive this row LOW
            time.sleep(0.00001)   # 10 microseconds for the column wires to settle

            for c, col in enumerate(self._cols):
                if col.is_active:                 # This column is LOW: the key at (r, c) looks pressed
                    time.sleep(DEBOUNCE_S)        # Wait out the contact bounce...
                    if col.is_active:             # ...and check again: still pressed, so it's real
                        while col.is_active:      # Wait for release so one press gives one key
                            time.sleep(0.01)
                        row.on()
                        return self._keymap[r][c]

            row.on()              # Back to HIGH before moving to the next row
        return None

    def close(self):
        for device in self._rows + self._cols:
            device.close()


# Test on its own: python3 keypad.py
if __name__ == "__main__":
    keypad = Keypad()
    print("Press keys on the keypad... (Ctrl+C to stop)")
    try:
        while True:
            key = keypad.scan()
            if key:
                print("Key:", key)
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        keypad.close()
