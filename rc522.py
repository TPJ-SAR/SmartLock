"""
rc522.py - Minimal driver for the MFRC522 (RC522) RFID reader on a Raspberry Pi.

It does one job: detect an ISO 14443A card or tag (the common 13.56 MHz key cards and fobs) and read its UID,
the card's ID number. That's all a smart lock needs to recognise a card.

Two conversations happen in this file:
  Pi  <-> RC522 chip : SPI register reads and writes (_read_reg, _write_reg). The chip has a 64-byte FIFO buffer
                        that holds the data going to or coming from the card.
  RC522 <-> card     : a 13.56 MHz radio link (ISO 14443A). The Pi fills the FIFO and tells the chip to transmit;
                        the chip sends the bytes over the air and puts the card's answer back in the FIFO.

Reading a UID takes two card commands (see read_uid):
  REQA      "is any card there?"  -> the card answers with 2 bytes (ATQA)
  Anticoll  "tell me your UID"    -> the card answers with 4 UID bytes and 1 check byte (BCC)

Passive cards have no battery: they run on energy from the reader's radio field, so the antenna stays on.

Register addresses and bit values come from the NXP MFRC522 datasheet.

Wiring (Raspberry Pi header, SPI0). Power the module from 3.3 V only: the RC522 is not 5 V tolerant.
  RC522 pin   Pi pin   Pi GPIO
  SDA (CS)    24       GPIO8  (CE0)
  SCK         23       GPIO11 (SCLK)
  MOSI        19       GPIO10 (MOSI)
  MISO        21       GPIO9  (MISO)
  IRQ         -        not used
  GND         6        GND
  RST         22       GPIO25
  3.3V        1        3.3 V

Enable SPI first: sudo raspi-config -> Interface Options -> SPI -> Yes, then reboot.
Run this file on its own (python3 rc522.py) to test the reader: it prints the UID of each card you tap.
"""

import time

import spidev                        # Talks to the Pi's SPI hardware (/dev/spidev0.0)
from gpiozero import DigitalOutputDevice  # Drives the RST pin


# ---- MFRC522 registers (datasheet section 9) ----
REG_COMMAND     = 0x01  # Starts and stops the chip's commands
REG_COM_IRQ     = 0x04  # Interrupt flags: "reply received", "timer ran out", ...
REG_ERROR       = 0x06  # Error flags from the last command
REG_FIFO_DATA   = 0x09  # Reading or writing here takes from or adds to the 64-byte FIFO
REG_FIFO_LEVEL  = 0x0A  # How many bytes are in the FIFO; writing 0x80 empties it
REG_BIT_FRAMING = 0x0D  # How many bits of the last byte to send; bit 7 starts the transmission
REG_COLL        = 0x0E  # Collision detection (two cards answering at once)
REG_MODE        = 0x11  # General settings, including the CRC start value
REG_TX_CONTROL  = 0x14  # Antenna driver pins (bits 0-1 switch the antenna on)
REG_TX_ASK      = 0x15  # Modulation setting
REG_T_MODE      = 0x2A  # Internal timer settings, used as a "no card answered" timeout
REG_T_PRESCALER = 0x2B
REG_T_RELOAD_H  = 0x2C
REG_T_RELOAD_L  = 0x2D
REG_VERSION     = 0x37  # Chip version: 0x91 or 0x92 on a genuine MFRC522, other values on clones

# ---- MFRC522 commands ----
CMD_IDLE       = 0x00  # Stop whatever the chip is doing
CMD_TRANSCEIVE = 0x0C  # Send the FIFO over the air, then receive the card's answer into the FIFO

# ---- ISO 14443A card commands ----
PICC_REQA         = 0x26  # Request: wake up any idle card in the field
PICC_ANTICOLL_CL1 = 0x93  # Anticollision, cascade level 1: ask for the (first 4 bytes of the) UID


class RC522:
    """An RC522 reader on SPI. Create it once, then call read_uid() in a loop."""

    def __init__(self, bus=0, device=0, rst_gpio=25, spi_hz=1_000_000):
        # bus=0, device=0 means /dev/spidev0.0: SPI0 with chip select CE0 (GPIO8)

        # Hard reset: pull RST low for a moment, then high, then give the chip's oscillator time to start
        self._rst = DigitalOutputDevice(rst_gpio, initial_value=False)
        time.sleep(0.001)
        self._rst.on()
        time.sleep(0.05)

        self._spi = spidev.SpiDev()
        self._spi.open(bus, device)
        self._spi.max_speed_hz = spi_hz  # The chip allows up to 10 MHz; 1 MHz copes with long jumper wires
        self._spi.mode = 0               # SPI mode 0: clock idles low, data read on the rising edge

        # The chip's timer gives up waiting for a card after 25 ms.
        # Tick rate: 13.56 MHz / (2 * 169 + 1) = 40 kHz, so a reload value of 1000 ticks (0x03E8) = 25 ms
        self._write_reg(REG_T_MODE, 0x80)       # Timer starts by itself at the end of each transmission
        self._write_reg(REG_T_PRESCALER, 0xA9)  # 169 -> 40 kHz ticks
        self._write_reg(REG_T_RELOAD_H, 0x03)   # 1000 ticks = 25 ms
        self._write_reg(REG_T_RELOAD_L, 0xE8)
        self._write_reg(REG_TX_ASK, 0x40)       # 100% ASK modulation, which ISO 14443A requires
        self._write_reg(REG_MODE, 0x3D)         # CRC start value 0x6363, as ISO 14443-3 specifies
        self._set_bits(REG_TX_CONTROL, 0x03)    # Antenna on

        # With nothing connected, MISO floats and reads as all 0s or all 1s, so this also checks the wiring
        version = self._read_reg(REG_VERSION)
        if version in (0x00, 0xFF):
            raise RuntimeError(f"No RC522 answered (version register 0x{version:02X}): check the wiring and that SPI is on")
        self.version = version

    # ---------- SPI register access (Pi <-> RC522) ----------
    # The first SPI byte is the address: bit 7 = 1 to read / 0 to write, bits 6-1 = register, bit 0 = always 0.
    # That's why the register number is shifted left by one and masked with 0x7E.

    def _write_reg(self, reg, value):
        self._spi.xfer2([(reg << 1) & 0x7E, value])

    def _read_reg(self, reg):
        # SPI sends and receives at the same time: the register's value comes back while we send a dummy 0x00
        return self._spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0x00])[1]

    def _set_bits(self, reg, mask):
        # Change only the bits in mask and leave the rest of the register as it was
        self._write_reg(reg, self._read_reg(reg) | mask)

    def _clear_bits(self, reg, mask):
        self._write_reg(reg, self._read_reg(reg) & ~mask & 0xFF)

    # ---------- Radio exchange (RC522 <-> card) ----------

    def _transceive(self, data, last_bits=0):
        """Send bytes to the card and return its reply as a list of bytes, or None if there was no valid reply.
        last_bits says how many bits of the last byte to send (0 = all 8)."""
        # Stop anything in progress, clear old flags, empty the FIFO, then load the bytes to send
        self._write_reg(REG_COMMAND, CMD_IDLE)
        self._write_reg(REG_COM_IRQ, 0x7F)
        self._write_reg(REG_FIFO_LEVEL, 0x80)
        for byte in data:
            self._write_reg(REG_FIFO_DATA, byte)
        self._write_reg(REG_BIT_FRAMING, last_bits)
        self._write_reg(REG_COMMAND, CMD_TRANSCEIVE)
        self._set_bits(REG_BIT_FRAMING, 0x80)  # StartSend: transmit now

        # Wait for the card's reply, or for the chip's 25 ms timer to say nobody answered
        received = False
        deadline = time.monotonic() + 0.035
        while time.monotonic() < deadline:
            irq = self._read_reg(REG_COM_IRQ)
            if irq & 0x30:   # RxIRq or IdleIRq: a reply came in
                received = True
                break
            if irq & 0x01:   # TimerIRq: no card answered
                break
        self._clear_bits(REG_BIT_FRAMING, 0x80)

        if not received:
            return None
        if self._read_reg(REG_ERROR) & 0x13:  # Buffer overflow, parity error or protocol error
            return None

        # The FIFO level is the number of bytes the card sent back
        count = self._read_reg(REG_FIFO_LEVEL)
        return [self._read_reg(REG_FIFO_DATA) for _ in range(count)]

    def read_uid(self):
        """Return the UID of a card on the reader as a hex string like '04A1B2C3', or None if there isn't one.

        Note: the card is never fully "selected", so a card left lying on the reader alternates between answering
        and not answering. Code that needs to know when a card is removed should wait for several misses in a row
        (smart_lock.py does this)."""
        self._clear_bits(REG_COLL, 0x80)

        # REQA is a short 7-bit frame. Any card in the field answers with 2 bytes (ATQA)
        atqa = self._transceive([PICC_REQA], last_bits=0x07)
        if atqa is None or len(atqa) != 2:
            return None

        # Anticollision: 0x20 means "I'm sending no UID bits, so give me all of yours".
        # The card answers with 4 UID bytes followed by BCC
        reply = self._transceive([PICC_ANTICOLL_CL1, 0x20])
        if reply is None or len(reply) != 5:
            return None

        # BCC is the XOR of the 4 UID bytes: if it doesn't match, the radio reply was corrupted
        if reply[0] ^ reply[1] ^ reply[2] ^ reply[3] != reply[4]:
            return None

        return "".join(f"{b:02X}" for b in reply[:4])

    def close(self):
        """Switch the antenna off and release the SPI device and the RST pin."""
        self._clear_bits(REG_TX_CONTROL, 0x03)
        self._spi.close()
        self._rst.close()


# Test on its own: python3 rc522.py
if __name__ == "__main__":
    reader = RC522()
    print(f"RC522 ready (version 0x{reader.version:02X}). Tap a card... (Ctrl+C to stop)")
    present = None  # UID of the card currently on the reader
    misses = 0      # Polls in a row with no answer (see the note in read_uid)
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
