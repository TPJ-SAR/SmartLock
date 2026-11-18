# Smart Lock: RFID reader and keypad on a Raspberry Pi

Basic Python code for the two inputs of a smart lock:

| File | What it is |
|---|---|
| `pn532.py` | Driver for the PN532 NFC/RFID reader (SPI), e.g. the red "PN532 NFC Module V3". Reads the UID of 13.56 MHz cards and fobs. **This is the reader the project uses.** |
| `rc522.py` | Driver for the RC522 RFID reader (SPI). Same interface, kept as an alternative reader. |
| `keypad.py` | Driver for a 4×4 matrix keypad (8 GPIO pins). |
| `smart_lock.py` | Demo lock: opens with a known card or the right PIN, with a lockout after wrong PINs. Pick the reader with `READER` at the top. |

Every file has comments that explain how it works. Each driver can also be run on its own to test the hardware.

## Hardware

- Raspberry Pi with the 40-pin header (spidev and gpiozero support the Pi 3, 4 and 5)
- PN532 NFC Module V3 (red board) and a card or fob (ISO 14443A / MIFARE). An RC522 module also works, see below
- 4×4 matrix keypad
- For the lock output: an LED with a 330 Ω resistor to try it out, or a relay module for a real electric lock

## Wiring

**PN532 NFC Module V3 → Pi** (SPI0)

1. **Set the DIP switch to SPI** before powering up: switch 1 **OFF**, switch 2 **ON** (the table printed on the board: `SPI 0 1`). In any other position the reader won't answer over SPI.
2. Use the **8-pin header** along the top edge (SCK MISO MOSI SS VCC GND IRQ RSTO). The 4-pin header on the side (GND VCC SDA SCL) is for I2C and stays unconnected.
3. **Power it from 3.3 V.** The board accepts 5 V, but then its signals can be 5 V, and the Pi's pins are not 5 V tolerant.

| PN532 | Pi pin | GPIO |
|---|---|---|
| SCK | 23 | GPIO11 (SCLK) |
| MISO | 21 | GPIO9 (MISO) |
| MOSI | 19 | GPIO10 (MOSI) |
| SS | 24 | GPIO8 (CE0) |
| VCC | 1 | 3.3 V |
| GND | 6 | GND |
| IRQ | not connected | |
| RSTO | not connected | |

IRQ isn't needed because `pn532.py` polls the PN532's status byte. RSTO is a reset *output* from the PN532 (for other chips), not a reset input, so it stays unconnected too.

<details>
<summary>Using an RC522 instead (set <code>READER = "RC522"</code> in <code>smart_lock.py</code>)</summary>

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

</details>

**Keypad → Pi**

| Keypad | R1 | R2 | R3 | R4 | C1 | C2 | C3 | C4 |
|---|---|---|---|---|---|---|---|---|
| Pi pin | 29 | 31 | 33 | 35 | 32 | 36 | 38 | 40 |
| GPIO | 5 | 6 | 13 | 19 | 12 | 16 | 20 | 21 |

**Lock output:** GPIO17 (pin 11) goes HIGH while unlocked. Drive a relay module from it, never a lock coil directly.

**No pin conflicts:** the reader uses SPI0 (pins 19, 21, 23, 24), the keypad pins 29–40, and the lock pin 11. Careful not to mix up *pin* numbers with *GPIO* numbers: Pi pin 19 (GPIO10, MOSI) is not the same as GPIO19 (pin 35, keypad R4).

## Setup

```bash
sudo raspi-config          # Interface Options -> SPI -> Yes, then reboot
sudo apt install python3-spidev python3-gpiozero
```

(Raspberry Pi OS usually has gpiozero already. With a virtual environment: `pip install -r requirements.txt`.)

## Run

```bash
python3 pn532.py        # Test the reader: tap a card, its UID is printed (python3 rc522.py for an RC522)
python3 keypad.py       # Test the keypad: each key you press is printed
python3 smart_lock.py   # The lock
```

1. Run `pn532.py`, tap your card, and copy its UID into `AUTHORIZED_CARDS` in `smart_lock.py`.
2. Change `PIN_CODE`.
3. Run `smart_lock.py`. Tap the card, or type the PIN and press `#`. `*` clears the PIN.

If `pn532.py` says no PN532 answered: check the DIP switch (SPI = switch 2 ON), that SPI is enabled, and the MISO/MOSI wires (they are not crossed: MISO goes to MISO).

If `keypad.py` prints the wrong characters, your keypad's pins are in a different order: swap wires or edit `KEYMAP` in `keypad.py`.

## Adding the wireless part

All events (unlock, refused card, wrong PIN, lockout) go through `report_event()` in `smart_lock.py`. Send them from there over MQTT, HTTP, Bluetooth or another link. A remote "unlock" command could call `SmartLock.unlock()`.

## Limitations

- The lock only checks the card's UID. That's enough to tell cards apart, but a UID can be copied, so a real product would also authenticate the card (for example MIFARE DESFire with keys). The PN532 reads full 7-byte UIDs; `rc522.py` reads only the first 4 bytes, so the same card can print a different UID on each reader.
- While the lock is open (`UNLOCK_SECONDS`) and while a key is held down, the program does nothing else.
- Card list and PIN are in the source code; a real lock would store them in a protected file or database.
