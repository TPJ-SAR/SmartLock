# Smart Lock: RFID reader and keypad on a Raspberry Pi

Basic Python code for the two inputs of a smart lock:

| File | What it is |
|---|---|
| `rc522.py` | Driver for the RC522 RFID reader (SPI). Reads the UID of 13.56 MHz cards and fobs. |
| `keypad.py` | Driver for a 4×4 matrix keypad (8 GPIO pins). |
| `smart_lock.py` | Demo lock: opens with a known card or the right PIN, with a lockout after wrong PINs. |

Every file has comments that explain how it works. Each driver can also be run on its own to test the hardware.

## Hardware

- Raspberry Pi with the 40-pin header (spidev and gpiozero support the Pi 3, 4 and 5)
- RC522 RFID module and a card or fob (ISO 14443A / MIFARE)
- 4×4 matrix keypad
- For the lock output: an LED with a 330 Ω resistor to try it out, or a relay module for a real electric lock

## Wiring

**RC522 → Pi** (3.3 V only, the RC522 is not 5 V tolerant)

| RC522 | Pi pin | GPIO |
|---|---|---|
| SDA | 24 | GPIO8 (CE0) |
| SCK | 23 | GPIO11 |
| MOSI | 19 | GPIO10 |
| MISO | 21 | GPIO9 |
| IRQ | not connected | |
| GND | 6 | GND |
| RST | 22 | GPIO25 |
| 3.3V | 1 | 3.3 V |

**Keypad → Pi**

| Keypad | R1 | R2 | R3 | R4 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|---|---|---|
| Pi pin | 29 | 31 | 33 | 35 | 32 | 36 | 38 | 40 |
| GPIO | 5 | 6 | 13 | 19 | 12 | 16 | 20 | 21 |

**Lock output:** GPIO17 (pin 11) goes HIGH while unlocked. Drive a relay module from it, never a lock coil directly.

## Setup

```bash
sudo raspi-config          # Interface Options -> SPI -> Yes, then reboot
sudo apt install python3-spidev python3-gpiozero
```

(Raspberry Pi OS usually has gpiozero already. With a virtual environment: `pip install -r requirements.txt`.)

## Run

```bash
python3 rc522.py        # Test the reader: tap a card, its UID is printed
python3 keypad.py       # Test the keypad: each key you press is printed
python3 smart_lock.py   # The lock
```

1. Run `rc522.py`, tap your card, and copy its UID into `AUTHORIZED_CARDS` in `smart_lock.py`.
2. Change `PIN_CODE`.
3. Run `smart_lock.py`. Tap the card, or type the PIN and press `#`. `*` clears the PIN.

If `keypad.py` prints the wrong characters, your keypad's pins are in a different order: swap wires or edit `KEYMAP` in `keypad.py`.

## Adding the wireless part

All events (unlock, refused card, wrong PIN, lockout) go through `report_event()` in `smart_lock.py`. Send them from there over MQTT, HTTP, Bluetooth or another link. A remote "unlock" command could call `SmartLock.unlock()`.

## Limitations

- Only the first 4 bytes of the UID are read. That's enough to tell cards apart, but a UID can be copied, so a real product would also authenticate the card (for example MIFARE DESFire with keys).
- While the lock is open (`UNLOCK_SECONDS`) and while a key is held down, the program does nothing else.
- Card list and PIN are in the source code; a real lock would store them in a protected file or database.
