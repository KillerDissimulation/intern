#!/usr/bin/env python3
"""
M328 / M328GW ISO15693 test system for Raspberry Pi.

Behaviour:
  Reader 1 -> read text from test memory
  Reader 2 -> write "chess board"
  Reader 3 -> write "checkers board"
  Reader 4 -> clear/reset ONLY the test memory area

Reverse-engineered from the supplied M328TestTools.exe + HFReader.dll.
The card UID is never modified.

IMPORTANT:
- Use a blank/test ISO15693 card first.
- This script reserves user-memory blocks 0..3 (16 bytes).
- Reader addresses are assumed to be 1,2,3,4.
- Gateway address is assumed to be 1.
"""

import time
import serial

PORT = "/dev/ttyUSB0"
BAUD = 115200
GATEWAY_ADDR = 1
READERS = (1, 2, 3, 4)

# 4 blocks x 4 bytes = 16 bytes.
START_BLOCK = 0
BLOCK_COUNT = 4
DATA_SIZE = BLOCK_COUNT * 4

POLL_DELAY = 0.12
RESPONSE_TIMEOUT = 0.8
DEBUG = False  # Change to True if you want to see every TX/RX frame.

# Generic ISO15693 command IDs used by HFReader.dll.
CMD_GET_UID = 0x11
CMD_READ_BLOCK = 0x22
CMD_WRITE_BLOCK = 0x23

# M328GW command used by M328TestTools to enable reader pass-through.
CMD_ENABLE_PASSTHROUGH = 0xFF


def crc16_x25(data: bytes) -> int:
    """CRC used by HFReader.dll / M328 frames."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x8408
            else:
                crc >>= 1
    return (~crc) & 0xFFFF


def build_frame(src_addr: int, target_addr: int, cmd: int, payload: bytes = b"") -> bytes:
    """
    Short 7E 55 frame equivalent to the manufacturer's formatter with:
      rspFrame = 0, longFrame = 0, RFU = 0.
    """
    frame = bytearray(b"\x7E\x55\x00")
    frame += int(src_addr).to_bytes(2, "little")
    frame += int(target_addr).to_bytes(2, "little")
    frame += bytes((cmd & 0xFF, 0x00))
    frame += payload

    # Manufacturer format: short-frame length = current frame length - 1.
    frame[2] = len(frame) - 1

    crc = crc16_x25(frame[2:])
    frame += crc.to_bytes(2, "little")
    return bytes(frame)


def read_exact(ser: serial.Serial, count: int, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    out = bytearray()
    while len(out) < count and time.monotonic() < deadline:
        chunk = ser.read(count - len(out))
        if chunk:
            out += chunk
        else:
            time.sleep(0.005)
    return bytes(out)


def read_frame(ser: serial.Serial, timeout: float = RESPONSE_TIMEOUT) -> bytes:
    """Read one short 7E55 frame and validate its CRC."""
    deadline = time.monotonic() + timeout
    state = 0

    while time.monotonic() < deadline:
        b = ser.read(1)
        if not b:
            continue

        if state == 0:
            if b[0] == 0x7E:
                state = 1
        else:
            if b[0] == 0x55:
                break
            state = 1 if b[0] == 0x7E else 0
    else:
        return b""

    length_byte = read_exact(ser, 1, max(0.05, deadline - time.monotonic()))
    if len(length_byte) != 1:
        return b""

    # Total frame size = short_length + 3.
    # We already have 7E 55 + length, so exactly `length` bytes remain.
    tail = read_exact(ser, length_byte[0], max(0.05, deadline - time.monotonic()))
    if len(tail) != length_byte[0]:
        return b""

    frame = b"\x7E\x55" + length_byte + tail

    if len(frame) < 5:
        return b""

    expected = int.from_bytes(frame[-2:], "little")
    actual = crc16_x25(frame[2:-2])
    if expected != actual:
        print(f"[WARN] CRC mismatch: {frame.hex(' ').upper()}")
        return b""

    return frame


def transact(ser: serial.Serial, target_addr: int, cmd: int, payload: bytes = b"", timeout: float = RESPONSE_TIMEOUT) -> bytes:
    frame = build_frame(0, target_addr, cmd, payload)
    ser.reset_input_buffer()
    if DEBUG:
        print(f"TX -> addr {target_addr}, cmd 0x{cmd:02X}: {frame.hex(' ').upper()}")
    ser.write(frame)
    ser.flush()
    resp = read_frame(ser, timeout)
    if DEBUG:
        print("RX <-", resp.hex(" ").upper() if resp else "<NO RESPONSE>")
    return resp


def response_status(frame: bytes):
    """
    Manufacturer parsing checks response[-4] as status/error byte.
    0 means success.
    """
    if len(frame) < 4:
        return None
    return frame[-4]


def enable_passthrough(ser: serial.Serial) -> None:
    print(f"Enabling M328 reader pass-through through gateway {GATEWAY_ADDR}...")
    resp = transact(ser, GATEWAY_ADDR, CMD_ENABLE_PASSTHROUGH)

    if not resp:
        raise RuntimeError("No response from gateway when enabling pass-through.")

    status = response_status(resp)
    print("Gateway response:", resp.hex(" ").upper())

    if status != 0:
        raise RuntimeError(f"Gateway rejected pass-through command. Status={status}")

    print("Pass-through enabled.\n")


def get_uid(ser: serial.Serial, reader_addr: int):
    """
    Direct ISO15693 inventory used by HFReader.dll:
      command = 0x11
      payload = one-byte mode (0x00)

    Manufacturer parser treats each returned tag record as 9 bytes and
    reads the 8 UID bytes beginning at response offset 10.
    """
    resp = transact(ser, reader_addr, CMD_GET_UID, b"\x00", timeout=0.45)
    if not resp:
        return None

    # No-tag replies can be short; only parse complete tag records.
    if len(resp) < 18:
        return None

    # Native DLL calculates tag count as (rx_len - 14) / 9.
    tag_count = max(0, (len(resp) - 14) // 9)
    if tag_count < 1:
        return None

    uid_raw = bytes(resp[10:18])
    if len(uid_raw) != 8 or all(b == 0 for b in uid_raw):
        return None

    return uid_raw


def read_blocks(ser: serial.Serial, reader_addr: int, uid_raw: bytes) -> bytes:
    # Wire format used by HFReader.dll: raw/RF UID bytes + start block + count.
    payload = uid_raw + bytes((START_BLOCK, BLOCK_COUNT))
    resp = transact(ser, reader_addr, CMD_READ_BLOCK, payload, timeout=1.2)

    if not resp:
        raise RuntimeError("No response while reading card memory")

    status = response_status(resp)
    if status != 0:
        raise RuntimeError(f"Read failed, status={status}; response={resp.hex(' ').upper()}")

    # HFReader.dll copies read data starting at response byte offset 10.
    data = bytes(resp[10:10 + DATA_SIZE])
    if len(data) != DATA_SIZE:
        raise RuntimeError(
            f"Read returned {len(data)} data bytes, expected {DATA_SIZE}; "
            f"response={resp.hex(' ').upper()}"
        )
    return data


def write_blocks(ser: serial.Serial, reader_addr: int, uid_raw: bytes, data: bytes) -> None:
    if len(data) != DATA_SIZE:
        raise ValueError(f"data must be exactly {DATA_SIZE} bytes")

    # Manufacturer write frame payload:
    #   UID[8] + startBlock[1] + blockCount[1] + blockData[blockCount*4]
    payload = uid_raw + bytes((START_BLOCK, BLOCK_COUNT)) + data
    resp = transact(ser, reader_addr, CMD_WRITE_BLOCK, payload, timeout=2.0)

    if not resp:
        raise RuntimeError("No response while writing card memory")

    status = response_status(resp)
    if status != 0:
        raise RuntimeError(f"Write failed, status={status}; response={resp.hex(' ').upper()}")


def encode_text(text: str) -> bytes:
    raw = text.encode("utf-8")
    if len(raw) > DATA_SIZE:
        raise ValueError(f"'{text}' is too long for the {DATA_SIZE}-byte test area")
    return raw.ljust(DATA_SIZE, b"\x00")


def decode_text(data: bytes) -> str:
    raw = data.split(b"\x00", 1)[0]
    if not raw:
        return ""
    return raw.decode("utf-8", errors="replace")


def show_uid(uid_raw: bytes) -> str:
    # M328TestTools reverses the RF-order UID for display.
    return uid_raw[::-1].hex().upper()


def handle_card(ser: serial.Serial, reader: int, uid_raw: bytes) -> None:
    uid = show_uid(uid_raw)
    print("=" * 58)

    try:
        if reader == 1:
            data = read_blocks(ser, reader, uid_raw)
            text = decode_text(data)

            print("[READER 1] READ DONE")
            print(f"Card UID         : {uid}")
            print(f"Card Information : {text if text else '<EMPTY>'}")
            print(f"Raw Data         : {data.hex(' ').upper()}")

        elif reader == 2:
            text = "chess board"
            write_blocks(ser, reader, uid_raw, encode_text(text))

            print("[READER 2] WRITE DONE")
            print(f"Card UID         : {uid}")
            print(f"Written Data     : {text}")

        elif reader == 3:
            text = "checkers board"
            write_blocks(ser, reader, uid_raw, encode_text(text))

            print("[READER 3] WRITE DONE")
            print(f"Card UID         : {uid}")
            print(f"Written Data     : {text}")

        elif reader == 4:
            write_blocks(ser, reader, uid_raw, b"\x00" * DATA_SIZE)

            print("[READER 4] CLEAR DONE")
            print(f"Card UID         : {uid}")
            print("Card Information : <EMPTY>")

    except Exception as exc:
        print(f"[READER {reader}] ERROR")
        print("Reason:", exc)

    print("=" * 58)
    print()


def main():
    print("M328 Board Card Test")
    print("Reader 1 -> READ")
    print('Reader 2 -> WRITE "chess board"')
    print('Reader 3 -> WRITE "checkers board"')
    print("Reader 4 -> CLEAR")
    print(f"Test memory -> blocks {START_BLOCK}..{START_BLOCK + BLOCK_COUNT - 1} ({DATA_SIZE} bytes)")
    print()

    with serial.Serial(PORT, BAUD, timeout=0.05) as ser:
        time.sleep(0.3)
        enable_passthrough(ser)

        last_uid = {reader: None for reader in READERS}

        print("Waiting for cards... Press Ctrl+C to stop.\n")

        try:
            while True:
                for reader in READERS:
                    try:
                        uid_raw = get_uid(ser, reader)
                    except Exception as exc:
                        print(f"[Reader {reader}] poll error: {exc}")
                        uid_raw = None

                    # Trigger only when a card newly appears / changes on that reader.
                    if uid_raw is not None and uid_raw != last_uid[reader]:
                        handle_card(ser, reader, uid_raw)

                    last_uid[reader] = uid_raw
                    time.sleep(0.03)

                time.sleep(POLL_DELAY)

        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    main()
