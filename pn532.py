"""
pn532.py - Minimal driver for the PN532 NFC/RFID reader (e.g. the red "PN532 NFC Module V3") on a Raspberry Pi, over SPI.

It does the same job as rc522.py and has the same interface (read_uid(), close(), info), so smart_lock.py can use
either reader: detect an ISO 14443A card or tag (13.56 MHz key cards, fobs, MIFARE) and read its UID.

How the PN532 differs from the RC522:
  - The RC522 is "dumb": the Pi drives the radio directly through the chip's registers.
  - The PN532 has its own firmware. The Pi sends it a command in a small packet (a "frame"), the PN532 does the
    whole radio exchange with the card by itself (including anticollision and selection) and sends back a reply frame.

Every exchange with the PN532 goes like this (see _command):
  1. Pi sends a command frame      00 00 FF LEN LCS D4 CMD data... DCS 00
  2. PN532 answers with an ACK     00 00 FF 00 FF 00        ("I got your command")
  3. PN532 answers with a reply    00 00 FF LEN LCS D5 CMD+1 data... DCS 00
  LEN = number of bytes from D4/D5 to the end of data, LCS makes LEN + LCS = 0, DCS makes (D4/D5 + data + DCS) = 0.

SPI details (PN532 user manual, section 6.2.5):
  - Every SPI transaction starts with one byte saying what it is: 0x01 data write, 0x02 status read, 0x03 data read.
  - A status read returns 0x01 when the PN532 has a frame ready for us.
  - The PN532 sends and expects bytes LSB first. The Pi's SPI hardware only does MSB first, so every byte is
    bit-reversed in software (_rev).

Board setup (PN532 NFC Module V3):
  - Set the two-way DIP switch to SPI: switch 1 OFF, switch 2 ON (the table printed on the board: SPI = 0 1).
  - Power it from 3.3 V so its logic levels are safe for the Pi (the Pi's pins are not 5 V tolerant).

Wiring (Raspberry Pi header, SPI0), using the 8-pin header at the top of the board:
  PN532 pin   Pi pin   Pi GPIO
  SCK         23       GPIO11 (SCLK)
  MISO        21       GPIO9  (MISO)
  MOSI        19       GPIO10 (MOSI)
  SS          24       GPIO8  (CE0)
  VCC         1        3.3 V
  GND         6        GND
  IRQ         -        not used (this driver polls the status byte instead)
  RSTO        -        not used (it's a reset OUTPUT from the PN532, not a reset input)

Enable SPI first: sudo raspi-config -> Interface Options -> SPI -> Yes, then reboot.
Run this file on its own (python3 pn532.py) to test the reader: it prints the UID of each card you tap.
"""

import time

import spidev  # Talks to the Pi's SPI hardware (/dev/spidev0.0)


# ---- SPI transaction types (first byte of each SPI transfer) ----
SPI_DATA_WRITE  = 0x01
SPI_STATUS_READ = 0x02
SPI_DATA_READ   = 0x03

# ---- Frame bytes ----
HOST_TO_PN532 = 0xD4   # TFI byte of frames we send
PN532_TO_HOST = 0xD5   # TFI byte of frames the PN532 sends
ACK_FRAME = [0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00]

# ---- PN532 commands (user manual, section 7) ----
CMD_GET_FIRMWARE_VERSION   = 0x02  # Reply: IC (0x32 = PN532), version, revision, supported card types
CMD_SAM_CONFIGURATION      = 0x14  # Must be sent once after power-up to put the PN532 in normal mode
CMD_RF_CONFIGURATION       = 0x32  # Used here to limit how long the PN532 searches for a card
CMD_IN_LIST_PASSIVE_TARGET = 0x4A  # "Find a card": does REQA, anticollision and select, returns the UID

BRTY_106K_TYPE_A = 0x00  # Card type for InListPassiveTarget: ISO 14443A at 106 kbps (MIFARE, NTAG, most fobs)


def _rev(byte):
    """Reverse the bit order of one byte (0b00000001 -> 0b10000000). Needed because the PN532 is LSB first."""
    return int(f"{byte:08b}"[::-1], 2)


class PN532:
    """A PN532 reader on SPI. Create it once, then call read_uid() in a loop."""

    def __init__(self, bus=0, device=0, spi_hz=1_000_000, search_retries=1):
        # bus=0, device=0 means /dev/spidev0.0: SPI0 with chip select CE0 (GPIO8)
        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = spi_hz  # The PN532 allows up to 5 MHz; 1 MHz copes with long jumper wires
        self._spi.mode = 0               # SPI mode 0: clock idles low, data read on the rising edge

        # Wake-up: the PN532 may be asleep after power-up. Holding SS low for ~2 ms wakes it
        self._spi.xfer2([0x00], spi_hz, 2000)
        time.sleep(0.01)

        # Ask for the firmware version. It also checks the wiring and the DIP switch: with no answer, nothing works
        firmware = None
        for _ in range(3):
            firmware = self._command(CMD_GET_FIRMWARE_VERSION, timeout=0.5)
            if firmware:
                break
            time.sleep(0.1)
        if not firmware or len(firmware) < 4 or firmware[0] != 0x32:
            self._spi.close()
            raise RuntimeError("No PN532 answered: check the wiring, that SPI is on, and that the DIP switch is set to SPI")
        self.version = firmware[1]
        self.info = f"PN532 firmware {firmware[1]}.{firmware[2]}"

        # SAMConfiguration: mode 0x01 = normal (no secure module), timeout 0x14 (only matters in other modes),
        # 0x01 = PN532 may use its IRQ pin (harmless when IRQ isn't wired)
        if self._command(CMD_SAM_CONFIGURATION, [0x01, 0x14, 0x01]) is None:
            raise RuntimeError("PN532 did not accept SAMConfiguration")

        # RFConfiguration item 0x05 = retry counts: [MxRtyATR, MxRtyPSL, MxRtyPassiveActivation].
        # By default the PN532 searches for a card forever (0xFF). A small value makes read_uid() return quickly
        # with "no card", so the main loop can keep checking the keypad
        self._command(CMD_RF_CONFIGURATION, [0x05, 0xFF, 0x01, search_retries])

    # ---------- SPI layer (Pi <-> PN532) ----------

    def _is_ready(self):
        # Status read: send 0x02, the next byte back is 0x01 when the PN532 has something for us
        reply = self._spi.xfer2([_rev(SPI_STATUS_READ), 0x00])
        return _rev(reply[1]) & 0x01 == 0x01

    def _wait_ready(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_ready():
                return True
            time.sleep(0.001)
        return False

    def _write(self, data):
        self._spi.xfer2([_rev(SPI_DATA_WRITE)] + [_rev(b) for b in data])

    def _read(self, count):
        # Data read: send 0x03, then dummy bytes while the PN532 clocks its frame out. The first byte back
        # arrives while we're still sending 0x03, so it's skipped
        reply = self._spi.xfer2([_rev(SPI_DATA_READ)] + [0x00] * count)
        return [_rev(b) for b in reply[1:]]

    # ---------- Frame layer ----------

    def _command(self, cmd, params=(), timeout=0.2):
        """Send one command and return the reply's data bytes (after D5 CMD+1), or None on timeout or error."""
        body = [HOST_TO_PN532, cmd] + list(params)
        length = len(body)
        frame = [0x00, 0x00, 0xFF, length, (-length) & 0xFF] + body + [(-sum(body)) & 0xFF, 0x00]
        self._write(frame)

        # 1) The ACK: proves the PN532 received the frame
        if not self._wait_ready(0.05) or self._read(len(ACK_FRAME)) != ACK_FRAME:
            return None

        # 2) The reply. If it doesn't come in time, send an ACK ourselves: that tells the PN532 to abort the
        #    command, so it's ready for the next one
        if not self._wait_ready(timeout):
            self._write(ACK_FRAME)
            return None

        raw = self._read(64)  # Longer than any reply we ask for; the extra bytes are just padding
        return self._parse_reply(raw, cmd)

    @staticmethod
    def _parse_reply(raw, cmd):
        # Find the start code 00 FF (there may be extra 00 bytes before it)
        for i in range(len(raw) - 1):
            if raw[i] == 0x00 and raw[i + 1] == 0xFF:
                break
        else:
            return None
        i += 2
        if i + 2 > len(raw):
            return None
        length, lcs = raw[i], raw[i + 1]
        if (length + lcs) & 0xFF != 0 or length < 2:
            return None                      # Length checksum wrong
        body = raw[i + 2:i + 2 + length]
        if len(body) != length or i + 2 + length >= len(raw):
            return None
        dcs = raw[i + 2 + length]
        if (sum(body) + dcs) & 0xFF != 0:
            return None                      # Data checksum wrong: the frame was corrupted
        if body[0] != PN532_TO_HOST or body[1] != cmd + 1:
            return None                      # Not the reply to our command (0x7F would be an error frame)
        return body[2:]

    # ---------- Card ----------

    def read_uid(self):
        """Return the UID of a card on the reader as a hex string like '04A1B2C3', or None if there isn't one.

        Unlike rc522.py, the PN532 does full anticollision, so 7-byte UIDs (NTAG stickers, some MIFARE cards) come
        back complete, as 14 hex characters. Most cheap fobs and white cards have 4-byte UIDs (8 hex characters)."""
        # MaxTg = 1 (only one card at a time), card type ISO 14443A 106 kbps
        reply = self._command(CMD_IN_LIST_PASSIVE_TARGET, [0x01, BRTY_106K_TYPE_A], timeout=0.2)
        # Reply: NbTg, Tg, SENS_RES (2 bytes), SEL_RES, UID length, UID...
        if not reply or reply[0] == 0 or len(reply) < 6:
            return None
        uid_len = reply[5]
        uid = reply[6:6 + uid_len]
        if len(uid) != uid_len or uid_len == 0:
            return None
        return "".join(f"{b:02X}" for b in uid)

    def close(self):
        """Release the SPI device."""
        self._spi.close()


# Test on its own: python3 pn532.py
if __name__ == "__main__":
    reader = PN532()
    print(f"{reader.info} ready. Tap a card... (Ctrl+C to stop)")
    present = None  # UID of the card currently on the reader
    misses = 0      # Polls in a row with no answer
    try:
        while True:
            uid = reader.read_uid()
            if uid:
                misses = 0
                if uid != present:       # A new tap: print it once
                    print("Card UID:", uid)
                    present = uid
            else:
                misses += 1
                if misses >= 3:          # Gone for 3 polls in a row: the card was taken away
                    present = None
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
