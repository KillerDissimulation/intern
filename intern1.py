import serial
import time
import json
import os
import threading

# =========================================================
# CONFIGURATION
# =========================================================

PORT = "/dev/ttyUSB0"
BAUD = 115200

DATABASE_FILE = "tag_database.json"

# Verified commands for your M328GW
START_SCAN = bytes.fromhex(
    "7E 55 09 00 00 01 00 40 00 00 42 22"
)

GET_RESULT = bytes.fromhex(
    "7E 55 08 00 00 01 00 41 00 99 11"
)

# Prevent multiple triggers while the same card remains
last_cards = {}

# Shared database
tag_database = {}

# Prevent database being accessed by two threads simultaneously
db_lock = threading.Lock()


# =========================================================
# DATABASE
# =========================================================

def load_database():

    global tag_database

    if not os.path.exists(DATABASE_FILE):
        tag_database = {}
        return

    try:
        with open(DATABASE_FILE, "r") as file:
            tag_database = json.load(file)

    except Exception as e:
        print(f"[DATABASE] Could not load database: {e}")
        tag_database = {}


def save_database():

    with db_lock:

        with open(DATABASE_FILE, "w") as file:
            json.dump(
                tag_database,
                file,
                indent=4
            )


def show_all_tags():

    print()
    print("========================================")
    print("         REGISTERED NFC TAGS")
    print("========================================")

    with db_lock:

        if not tag_database:
            print("No tags registered.")

        else:

            for uid, game in tag_database.items():

                print(
                    f"{uid}  ->  {game.upper()}"
                )

    print("========================================")
    print()


def clear_database():

    global tag_database

    with db_lock:
        tag_database = {}

        with open(DATABASE_FILE, "w") as file:
            json.dump({}, file, indent=4)


# =========================================================
# SERIAL
# =========================================================

ser = serial.Serial(
    PORT,
    BAUD,
    timeout=0.5
)

time.sleep(1)


def receive_response():

    time.sleep(0.1)

    return ser.read(512)


# =========================================================
# SCAN M328 READERS
# =========================================================

def scan():

    ser.reset_input_buffer()

    # Start scan
    ser.write(START_SCAN)
    receive_response()

    time.sleep(0.1)

    # Get results
    ser.write(GET_RESULT)
    response = receive_response()

    if len(response) < 13:
        return {}

    # Bytes 10-11 contain payload length
    payload_length = (
        response[10] |
        (response[11] << 8)
    )

    payload = response[
        12:12 + payload_length
    ]

    if len(payload) < 1:
        return {}

    reader_count = payload[0]

    if reader_count == 0:
        return {}

    record_size = (
        len(payload) - 1
    ) // reader_count

    detected = {}

    for i in range(reader_count):

        start = 1 + (
            i * record_size
        )

        record = payload[
            start:start + record_size
        ]

        if len(record) < 8:
            continue

        uid_raw = record[:8]

        # All zeros = no card
        if all(b == 0 for b in uid_raw):
            continue

        # M328TestTools reverses UID before displaying
        uid = uid_raw[::-1].hex().upper()

        detected[i + 1] = uid

    return detected


# =========================================================
# READER ACTIONS
# =========================================================

def reader_1(uid):

    with db_lock:
        game = tag_database.get(uid)

    print()
    print("========================================")
    print("[READER 1] READ DONE")
    print(f"Tag UID : {uid}")

    if game:

        print(f"Game    : {game.upper()}")

    else:

        print("Game    : UNREGISTERED")

    print("========================================")
    print()


def reader_2(uid):

    with db_lock:
        previous = tag_database.get(uid)

        tag_database[uid] = "Chess"

    save_database()

    print()
    print("========================================")
    print("[READER 2] REGISTRATION DONE")
    print(f"Tag UID : {uid}")
    print("Game    : CHESS")

    if previous and previous.lower() != "chess":
        print(f"Changed : {previous.upper()} -> CHESS")

    print("========================================")
    print()


def reader_3(uid):

    with db_lock:
        previous = tag_database.get(uid)

        tag_database[uid] = "Checkers"

    save_database()

    print()
    print("========================================")
    print("[READER 3] REGISTRATION DONE")
    print(f"Tag UID : {uid}")
    print("Game    : CHECKERS")

    if previous and previous.lower() != "checkers":
        print(
            f"Changed : {previous.upper()} -> CHECKERS"
        )

    print("========================================")
    print()


def reader_4(uid):

    print()
    print("========================================")
    print("[READER 4] CLEAR")
    print(f"Trigger Tag : {uid}")

    clear_database()

    print("ALL REGISTERED TAGS CLEARED")
    print("========================================")
    print()


def handle_card(reader, uid):

    if reader == 1:
        reader_1(uid)

    elif reader == 2:
        reader_2(uid)

    elif reader == 3:
        reader_3(uid)

    elif reader == 4:
        reader_4(uid)


# =========================================================
# KEYBOARD
# =========================================================

def keyboard_listener():

    while True:

        try:
            command = input().strip()

            if command == "1":
                show_all_tags()

        except EOFError:
            break


# =========================================================
# MAIN
# =========================================================

def main():

    global last_cards

    load_database()

    print()
    print("========================================")
    print("        M328 GAME TAG SYSTEM")
    print("========================================")
    print("Reader 1 -> Read tag")
    print("Reader 2 -> Register as CHESS")
    print("Reader 3 -> Register as CHECKERS")
    print("Reader 4 -> CLEAR ALL")
    print()
    print("Press 1 + ENTER -> Show all tags")
    print("Ctrl+C          -> Exit")
    print("========================================")
    print()
    print("Waiting for NFC tags...")
    print()

    # Keyboard runs separately so NFC scanning continues
    keyboard_thread = threading.Thread(
        target=keyboard_listener,
        daemon=True
    )

    keyboard_thread.start()

    try:

        while True:

            cards = scan()

            # Trigger only when a new card appears
            for reader, uid in cards.items():

                previous_uid = last_cards.get(reader)

                if previous_uid != uid:

                    handle_card(
                        reader,
                        uid
                    )

            # Update currently-present cards
            last_cards = cards.copy()

            time.sleep(0.25)

    except KeyboardInterrupt:

        print()
        print("Program stopped.")

    finally:

        ser.close()


if __name__ == "__main__":
    main()
