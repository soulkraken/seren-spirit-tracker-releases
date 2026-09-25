import csv
from difflib import SequenceMatcher
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import threading
import queue
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# Some Python 3.13 Windows installations do not initialize Tcl's executable
# path before tkinter starts. Initializing it here keeps both source and
# packaged launches reliable without changing the user's environment.
if sys.platform == "win32" and not getattr(sys, "frozen", False):
    try:
        import ctypes

        tcl_dll = Path(sys.base_prefix) / "DLLs" / "tcl86t.dll"
        if tcl_dll.exists():
            ctypes.CDLL(str(tcl_dll)).Tcl_FindExecutable(
                str(Path(sys.executable).resolve()).encode()
            )
    except Exception:
        pass

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import mss
import pytesseract
from PIL import Image, ImageTk


# ===== SETTINGS =====

DEFAULT_SETTINGS = {
    "monitor_number": 2,
    "region_left_offset": 0,
    "region_width": 450,
    "region_height": 480,
    "region_bottom_offset": 600,
    "check_every_seconds": 0.5,
}

if getattr(sys, "frozen", False):
    executable_dir = Path(sys.executable).resolve().parent
    # Updater-ready releases keep the main executable inside ``app`` while
    # the stable launcher and persistent ``data`` directory live one level up.
    APP_ROOT = (
        executable_dir.parent
        if executable_dir.name.casefold() == "app"
        else executable_dir
    )
    DATA_DIR = APP_ROOT / "data"
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", executable_dir / "runtime"))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Releases before the separated-data layout stored these files beside the
    # executable. Move them once so an in-place update preserves everything.
    for legacy_name in (
        "tracker_settings.json",
        "seren_spirit_rewards.csv",
        "catalyst_rewards.csv",
        "seren_spirit_rewards.db",
        "seren_spirit_rewards.db-wal",
        "seren_spirit_rewards.db-shm",
        "latest_ocr_debug_capture.png",
        "seren_watcher_error.log",
    ):
        legacy_path = APP_ROOT / legacy_name
        migrated_path = DATA_DIR / legacy_name
        if legacy_path.exists() and not migrated_path.exists():
            try:
                legacy_path.replace(migrated_path)
            except OSError:
                shutil.copy2(legacy_path, migrated_path)
else:
    APP_ROOT = Path(__file__).resolve().parent
    DATA_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = DATA_DIR

BASE_DIR = DATA_DIR
CONFIG_PATH = DATA_DIR / "tracker_settings.json"
CSV_PATH = DATA_DIR / "seren_spirit_rewards.csv"
CATALYST_CSV_PATH = DATA_DIR / "catalyst_rewards.csv"
DB_PATH = DATA_DIR / "seren_spirit_rewards.db"
DEBUG_IMAGE_PATH = DATA_DIR / "latest_ocr_debug_capture.png"
ERROR_LOG_PATH = DATA_DIR / "seren_watcher_error.log"
ICON_PATH = RESOURCE_DIR / "seren_spirit.ico"
ICON_PNG_PATH = RESOURCE_DIR / "seren_spirit_icon.png"
TITLE_ICON_PATH = RESOURCE_DIR / "seren_spirit_title_icon.png"
APP_USER_MODEL_ID = "SerenSpiritTracker.App"
GE_PRICE_API_URL = "https://api.weirdgloop.org/exchange/history/rs/latest"


def find_tesseract():
    """Locate the bundled OCR engine first, then common system installs."""
    candidates = [
        RESOURCE_DIR / "Tesseract-OCR" / "tesseract.exe",
        DATA_DIR / "Tesseract-OCR" / "tesseract.exe",
    ]

    configured = os.environ.get("TESSERACT_CMD")
    if configured:
        candidates.append(Path(configured))

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Tesseract-OCR" / "tesseract.exe")

    located = shutil.which("tesseract")
    if located:
        candidates.append(Path(located))

    for candidate in candidates:
        if candidate.is_file():
            tessdata = candidate.parent / "tessdata"
            if tessdata.is_dir():
                os.environ["TESSDATA_PREFIX"] = str(tessdata)
            return candidate

    return None


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for key in settings:
                if key in stored:
                    settings[key] = stored[key]
        except (OSError, ValueError, TypeError):
            pass
    return settings


def apply_settings(settings):
    global MONITOR_NUMBER, REGION_LEFT_OFFSET, REGION_WIDTH
    global REGION_HEIGHT, REGION_BOTTOM_OFFSET, CHECK_EVERY_SECONDS

    MONITOR_NUMBER = int(settings["monitor_number"])
    REGION_LEFT_OFFSET = int(settings["region_left_offset"])
    REGION_WIDTH = int(settings["region_width"])
    REGION_HEIGHT = int(settings["region_height"])
    REGION_BOTTOM_OFFSET = int(settings["region_bottom_offset"])
    CHECK_EVERY_SECONDS = float(settings["check_every_seconds"])


RUNTIME_SETTINGS = load_settings()
apply_settings(RUNTIME_SETTINGS)
TESSERACT_PATH = find_tesseract()
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_PATH)

COLOR_BG = "#0d0c09"
COLOR_PANEL = "#15130e"
COLOR_ROW_ALT = "#211c13"
COLOR_GOLD = "#d6a83f"
COLOR_GOLD_DARK = "#77551b"
COLOR_TEXT = "#eee3c5"
COLOR_MUTED = "#b9aa87"
COLOR_GREEN = "#72c83f"
COLOR_PRICE_MILLION = "#78d34b"
COLOR_PRICE_BILLION = "#63a9e8"
COLOR_RED = "#d85b43"
COLOR_SELECTION = "#4b3b18"

SPLIT_SINGLE_AND_BULK_ITEMS = {
    "Uncut dragonstone",
    "Huge plated rune salvage",
}

# Permanent storage order: never reorder or remove existing entries. New
# rewards must be appended so historical numeric IDs keep their meaning.
REWARD_NAMES = (
    "Battlestaff",
    "Blurberry Special",
    "Catalytic anima stone",
    "Ciku seed",
    "Crystal key",
    "Dark animica stone spirit",
    "Distraction & Diversion reset token (daily)",
    "Distraction & Diversion reset token (monthly)",
    "Distraction & Diversion reset token (weekly)",
    "Dragon bones",
    "Dragon helm",
    "Dragon longsword",
    "Dragon spear",
    "Ectoplasm",
    "Golden dragonfruit seed",
    "Hardened dragon bones",
    "Hazelmere's signet ring",
    "Huge plated rune salvage",
    "Large blunt necronium salvage",
    "Light animica stone spirit",
    "Loop half of a key",
    "Magic logs",
    "Magic seed",
    "Mahogany plank",
    "Medium spiky orikalkum salvage",
    "Off-hand dragon longsword",
    "Onyx bolt tips",
    "Prayer potion (4)",
    "Primal stone spirit",
    "Raw rocktail",
    "Rune arrowheads",
    "Shield left half",
    "Small bladed orikalkum salvage",
    "Soft clay",
    "Soul rune",
    "Starbloom flower seed",
    "Super restore (4)",
    "Teak plank",
    "Tooth half of a key",
    "Uncut diamond",
    "Uncut dragonstone",
    "Vecna skull",
    "Water talisman",
    "White berries",
    "Wine of Saradomin",
    "Yew logs",
)

VALID_REWARDS = frozenset(REWARD_NAMES)
REWARD_BY_ID = {
    reward_id: reward
    for reward_id, reward in enumerate(REWARD_NAMES, start=1)
}
REWARD_ID_BY_NAME = {
    reward: reward_id
    for reward_id, reward in REWARD_BY_ID.items()
}

AUTO_MATCH_THRESHOLD = 0.90
AUTO_MATCH_LEAD = 0.08


def console_log(*values):
    """Write diagnostics only when the app was launched with a console."""
    if sys.stdout is not None:
        print(*values)

TIMESTAMP_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\]")

REWARD_RE = re.compile(
    r"the\s+seren\s+spirit\s+gifts\s+you:\s*(\d+)\s*x\s*(.*?)\.\s*the\s+gift\s*is\s+sent\s*to\s+your\s*bank",
    re.IGNORECASE
)

LOOSE_REWARD_RE = re.compile(
    r"(\d+)\s*[xX×*]\s*(.+?)(?:\.\s*the\b|$)",
    re.IGNORECASE
)

CATALYST_DIFFICULTIES = ("easy", "medium", "hard", "elite", "master")
CATALYST_DIFFICULTY_ORDER = {
    difficulty: index
    for index, difficulty in enumerate(CATALYST_DIFFICULTIES)
}
CATALYST_ITEM_RE = re.compile(
    r"(?P<name>.+?)\s*\(\s*"
    r"(?P<difficulty>easy|medium|hard|elite|master)\s*\)",
    re.IGNORECASE
)
CATALYST_RE = re.compile(
    r"the\W*catalyst\W*of\W*alteration\W*contained\W*"
    r"(\d+)\s*[xX×*]\s*"
    r"(.+?\(\s*(?:easy|medium|hard|elite|master)\s*\))",
    re.IGNORECASE
)


# ===== OCR / CAPTURE =====

def capture_chatbox():
    with mss.mss() as sct:
        if MONITOR_NUMBER <= 0 or MONITOR_NUMBER >= len(sct.monitors):
            raise ValueError(
                f"Monitor {MONITOR_NUMBER} is unavailable. Open Setup and "
                "select one of the connected monitors."
            )
        monitor = sct.monitors[MONITOR_NUMBER]

        region = {
            "left": monitor["left"] + REGION_LEFT_OFFSET,
            "top": monitor["top"] + monitor["height"] - REGION_BOTTOM_OFFSET,
            "width": REGION_WIDTH,
            "height": REGION_HEIGHT,
        }

        screenshot = sct.grab(region)
        return Image.frombytes("RGB", screenshot.size, screenshot.rgb)


def read_text_from_image(image):
    return pytesseract.image_to_string(image, config="--psm 6")


# ===== MESSAGE PARSING =====

def combine_wrapped_chat_lines(raw_text):
    messages = []
    current_message = ""

    for line in raw_text.splitlines():
        line = line.strip()

        if not line:
            continue

        if TIMESTAMP_RE.match(line):
            if current_message:
                messages.append(current_message)

            current_message = line
        else:
            if current_message:
                current_message += " " + line
            else:
                current_message = line

    if current_message:
        messages.append(current_message)

    return messages


def normalize_message(message):
    return " ".join(message.split())


def get_message_timestamp(message):
    match = TIMESTAMP_RE.match(message)
    return match.group(0) if match else None


def is_target_message(message):
    lowered = message.lower()

    return re.search(
        r"^\[\d{2}:\d{2}:\d{2}\]\W*the\W*seren\W*spirit",
        lowered
    ) is not None


def is_catalyst_message(message):
    return re.search(
        r"^\[\d{2}:\d{2}:\d{2}\]\W*the\W*catalyst\W*of\W*"
        r"alteration\W*contained",
        message,
        re.IGNORECASE
    ) is not None


def is_birds_nest_message(message):
    return re.search(
        r"^\[\d{2}:\d{2}:\d{2}\]\W*you\W*find\W*a\W*"
        r"bird\W*s\W*nest\b",
        message,
        re.IGNORECASE
    ) is not None


def clean_catalyst_item(item):
    """Return only the item text through its recognised difficulty suffix."""
    normalized = " ".join(str(item).split()).strip()
    match = CATALYST_ITEM_RE.search(normalized)
    if not match:
        return None

    name = match.group("name").strip(" .:;|»¥")
    difficulty = match.group("difficulty").lower()
    if not name:
        return None
    return f"{name} ({difficulty})"


def catalyst_item_sort_key(item):
    match = re.search(
        r"\((easy|medium|hard|elite|master)\)$",
        item,
        re.IGNORECASE
    )
    difficulty = match.group(1).lower() if match else ""
    return (
        CATALYST_DIFFICULTY_ORDER.get(difficulty, len(CATALYST_DIFFICULTIES)),
        item.casefold(),
    )


def parse_catalyst_message(message):
    match = CATALYST_RE.search(message)
    if not match:
        return None

    quantity = int(match.group(1))
    item = clean_catalyst_item(match.group(2))
    if quantity <= 0 or not item:
        return None
    return quantity, item


def parse_reward_message(message):
    match = REWARD_RE.search(message)

    if not match:
        return None

    quantity = int(match.group(1))
    reward = match.group(2).strip()

    return quantity, reward


def extract_reward_components(message):
    """Extract quantity and reward text without requiring perfect boilerplate."""
    strict_result = parse_reward_message(message)
    if strict_result:
        return strict_result

    match = LOOSE_REWARD_RE.search(message)
    if match:
        return int(match.group(1)), match.group(2).strip()

    # Last-resort extraction keeps enough information for manual review when
    # OCR damages the quantity separator or the sentence ending.
    payload = message.split(":", 1)[1] if ":" in message else message
    payload = re.split(r"\.\s*the\b", payload, maxsplit=1, flags=re.IGNORECASE)[0]

    quantity_match = re.match(r"\s*(\d+)", payload)
    quantity = int(quantity_match.group(1)) if quantity_match else None

    if quantity_match:
        reward_text = payload[quantity_match.end():]
        reward_text = re.sub(
            r"^\s*(?:[xX×*]|[^A-Za-z0-9])\s*",
            "",
            reward_text,
            count=1
        )
    else:
        reward_text = payload

    reward_text = reward_text.strip(" .\t\r\n¥")
    return quantity, reward_text


def normalize_catalog_text(text):
    """Normalize harmless punctuation and spacing differences for matching."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def reward_similarity(ocr_reward, canonical_reward):
    ocr_normal = normalize_catalog_text(ocr_reward)
    canonical_normal = normalize_catalog_text(canonical_reward)

    normal_score = SequenceMatcher(None, ocr_normal, canonical_normal).ratio()
    compact_score = SequenceMatcher(
        None,
        ocr_normal.replace(" ", ""),
        canonical_normal.replace(" ", "")
    ).ratio()
    return max(normal_score, compact_score)


def rank_reward_matches(ocr_reward, limit=3):
    ranked = sorted(
        (
            (reward, reward_similarity(ocr_reward, reward))
            for reward in VALID_REWARDS
        ),
        key=lambda result: (-result[1], result[0].lower())
    )
    return ranked[:limit]


def choose_automatic_reward(ocr_reward, ranked_matches):
    if not ranked_matches:
        return None

    normalized_ocr = normalize_catalog_text(ocr_reward)
    for reward in VALID_REWARDS:
        if normalized_ocr == normalize_catalog_text(reward):
            return reward

    best_reward, best_score = ranked_matches[0]
    second_score = ranked_matches[1][1] if len(ranked_matches) > 1 else 0

    if (
        best_score >= AUTO_MATCH_THRESHOLD
        and best_score - second_score >= AUTO_MATCH_LEAD
    ):
        return best_reward

    return None


# ===== STORAGE =====

def migrate_legacy_csv_if_needed():
    """Convert the original verbose CSV schema to compact numeric storage."""
    if not CSV_PATH.exists():
        return None

    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        if fieldnames == ["t", "q", "i"]:
            return None

        if not {"timestamp", "quantity", "reward"}.issubset(fieldnames):
            raise ValueError(f"Unrecognized CSV columns: {fieldnames}")

        compact_rows = []
        for row_number, row in enumerate(reader, start=2):
            reward = row["reward"]
            reward_id = REWARD_ID_BY_NAME.get(reward)
            if reward_id is None:
                raise ValueError(
                    f"Cannot migrate row {row_number}: unknown reward {reward!r}"
                )

            unix_timestamp = int(
                datetime.strptime(
                    row["timestamp"],
                    "%Y-%m-%d %H:%M:%S"
                ).timestamp()
            )
            compact_rows.append((unix_timestamp, int(row["quantity"]), reward_id))

    backup_path = CSV_PATH.with_name(
        f"{CSV_PATH.stem}_legacy_backup_"
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        f"{CSV_PATH.suffix}"
    )
    backup_path.write_bytes(CSV_PATH.read_bytes())

    temporary_path = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["t", "q", "i"])
            writer.writerows(compact_rows)

        temporary_path.replace(CSV_PATH)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return backup_path


def ensure_csv_exists():
    if CSV_PATH.exists():
        migrate_legacy_csv_if_needed()
        return

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["t", "q", "i"])


def read_csv_mirror_rows():
    ensure_csv_exists()

    rows = []

    with CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            try:
                reward_id = int(row["i"])
                rows.append((int(row["t"]), int(row["q"]), reward_id))
            except (KeyError, TypeError, ValueError):
                # A malformed mirror row is omitted; SQLite will rebuild the
                # mirror from its authoritative copy on startup.
                continue

    return rows


def ensure_catalyst_csv_exists():
    if CATALYST_CSV_PATH.exists():
        return

    with CATALYST_CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["t", "q", "item"])


def read_catalyst_csv_mirror_rows():
    ensure_catalyst_csv_exists()
    rows = []

    with CATALYST_CSV_PATH.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                timestamp = int(row["t"])
                quantity = int(row["q"])
                item = clean_catalyst_item(row["item"])
                if quantity > 0 and item:
                    rows.append((timestamp, quantity, item))
            except (KeyError, TypeError, ValueError):
                continue

    return rows


def connect_database():
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def database_connection():
    connection = connect_database()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    """Create the database, import the CSV once, and repair its mirror."""
    csv_rows = read_csv_mirror_rows()
    catalyst_csv_rows = read_catalyst_csv_mirror_rows()

    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rewards (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS drops (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                reward_id INTEGER NOT NULL,
                FOREIGN KEY (reward_id) REFERENCES rewards(id)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_drops_timestamp ON drops(timestamp)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalyst_drops (
                id INTEGER PRIMARY KEY,
                timestamp INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                item TEXT NOT NULL CHECK (length(trim(item)) > 0)
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalyst_drops_timestamp "
            "ON catalyst_drops(timestamp)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS misc_counters (
                name TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0)
            )
            """
        )

        stored_rewards = dict(
            connection.execute("SELECT id, name FROM rewards").fetchall()
        )
        for reward_id, reward in REWARD_BY_ID.items():
            stored_name = stored_rewards.get(reward_id)
            if stored_name is not None and stored_name != reward:
                raise ValueError(
                    f"Reward ID {reward_id} is already assigned to {stored_name!r}, "
                    f"not {reward!r}"
                )
            connection.execute(
                "INSERT OR IGNORE INTO rewards (id, name) VALUES (?, ?)",
                (reward_id, reward)
            )

        drop_count = connection.execute("SELECT COUNT(*) FROM drops").fetchone()[0]
        if drop_count == 0 and csv_rows:
            connection.executemany(
                """
                INSERT INTO drops (timestamp, quantity, reward_id)
                VALUES (?, ?, ?)
                """,
                csv_rows
            )

        catalyst_count = connection.execute(
            "SELECT COUNT(*) FROM catalyst_drops"
        ).fetchone()[0]
        if catalyst_count == 0 and catalyst_csv_rows:
            connection.executemany(
                """
                INSERT INTO catalyst_drops (timestamp, quantity, item)
                VALUES (?, ?, ?)
                """,
                catalyst_csv_rows
            )

        # Older OCR captures sometimes included the following chat messages in
        # the stored item name. Keep only the text through the known difficulty
        # suffix and persist the repair to both SQLite and the CSV mirror.
        for drop_id, stored_item in connection.execute(
            "SELECT id, item FROM catalyst_drops"
        ).fetchall():
            cleaned_item = clean_catalyst_item(stored_item)
            if cleaned_item and cleaned_item != stored_item:
                connection.execute(
                    "UPDATE catalyst_drops SET item = ? WHERE id = ?",
                    (cleaned_item, drop_id)
                )

    sync_csv_mirror_from_database()
    sync_catalyst_csv_mirror_from_database()


def sync_csv_mirror_from_database():
    """Atomically rebuild the compact CSV mirror from SQLite."""
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT timestamp, quantity, reward_id
            FROM drops
            ORDER BY id
            """
        ).fetchall()

    temporary_path = CSV_PATH.with_suffix(f"{CSV_PATH.suffix}.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["t", "q", "i"])
            writer.writerows(rows)
        temporary_path.replace(CSV_PATH)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def append_csv_mirror_row(timestamp, quantity, reward_id):
    ensure_csv_exists()

    with CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow([int(timestamp), int(quantity), int(reward_id)])


def sync_catalyst_csv_mirror_from_database():
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT timestamp, quantity, item
            FROM catalyst_drops
            ORDER BY id
            """
        ).fetchall()

    temporary_path = CATALYST_CSV_PATH.with_suffix(".csv.tmp")
    try:
        with temporary_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(["t", "q", "item"])
            writer.writerows(rows)
        temporary_path.replace(CATALYST_CSV_PATH)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def append_catalyst_csv_mirror_row(timestamp, quantity, item):
    ensure_catalyst_csv_exists()
    with CATALYST_CSV_PATH.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow([int(timestamp), int(quantity), item])


def log_reward(script_timestamp, quantity, reward):
    reward_id = REWARD_ID_BY_NAME[reward]
    timestamp = int(script_timestamp)
    quantity = int(quantity)

    if not DB_PATH.exists():
        initialize_database()

    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO drops (timestamp, quantity, reward_id)
            VALUES (?, ?, ?)
            """,
            (timestamp, quantity, reward_id)
        )

    append_csv_mirror_row(timestamp, quantity, reward_id)


def read_csv_rows():
    """Read display rows from SQLite; retained name avoids a broad GUI rewrite."""
    if not DB_PATH.exists():
        initialize_database()

    rows = []

    with database_connection() as connection:
        for timestamp, quantity, reward in connection.execute(
            """
            SELECT drops.timestamp, drops.quantity, rewards.name
            FROM drops
            JOIN rewards ON rewards.id = drops.reward_id
            ORDER BY drops.id
            """
        ):
            rows.append({
                "timestamp": timestamp,
                "quantity": quantity,
                "reward": reward,
            })

    return rows


def log_catalyst_drop(script_timestamp, quantity, item):
    timestamp = int(script_timestamp)
    quantity = int(quantity)
    item = clean_catalyst_item(item)
    if quantity <= 0 or not item:
        raise ValueError("Catalyst drop does not contain a valid item and difficulty")

    if not DB_PATH.exists():
        initialize_database()

    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO catalyst_drops (timestamp, quantity, item)
            VALUES (?, ?, ?)
            """,
            (timestamp, quantity, item)
        )

    append_catalyst_csv_mirror_row(timestamp, quantity, item)


def read_catalyst_rows():
    if not DB_PATH.exists():
        initialize_database()

    rows = []
    with database_connection() as connection:
        for timestamp, quantity, item in connection.execute(
            """
            SELECT timestamp, quantity, item
            FROM catalyst_drops
            ORDER BY id
            """
        ):
            rows.append({
                "timestamp": timestamp,
                "quantity": quantity,
                "item": item,
            })
    return rows


def increment_misc_counter(name):
    if not DB_PATH.exists():
        initialize_database()

    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO misc_counters (name, count)
            VALUES (?, 1)
            ON CONFLICT(name) DO UPDATE SET count = count + 1
            """,
            (name,)
        )
        return connection.execute(
            "SELECT count FROM misc_counters WHERE name = ?",
            (name,)
        ).fetchone()[0]


def read_misc_counter(name):
    if not DB_PATH.exists():
        initialize_database()

    with database_connection() as connection:
        row = connection.execute(
            "SELECT count FROM misc_counters WHERE name = ?",
            (name,)
        ).fetchone()
    return int(row[0]) if row else 0


def get_summary_keys(raw_reward, quantity):
    """
    Returns two names:
    - internal_key: used for grouping/sorting/counting
    - display_reward: shown in the GUI

    For split items like Uncut dragonstone:
    - quantity 1 is grouped separately from quantity > 1
    - both still display as "Uncut dragonstone"
    """

    if raw_reward in SPLIT_SINGLE_AND_BULK_ITEMS:
        if quantity == 1:
            return f"{raw_reward}__single", raw_reward
        else:
            return f"{raw_reward}__bulk", raw_reward

    return raw_reward, raw_reward


def format_gp(value):
    """Format coins with k/m/b suffixes, truncated to two decimal places."""
    value = int(value)

    for divisor, suffix in ((1_000_000_000, "b"), (1_000_000, "m"), (1_000, "k")):
        if value >= divisor:
            truncated = math.floor((value / divisor) * 100) / 100
            return f"{truncated:.2f}{suffix}"

    return f"{value:,}"


def fetch_ge_prices(item_names):
    """Fetch current RS3 guide prices for exact item names."""
    names = sorted(set(item_names), key=str.lower)
    if not names:
        return {}

    query = urlencode({"name": "|".join(names)})
    request = Request(
        f"{GE_PRICE_API_URL}?{query}",
        headers={"User-Agent": "SerenSpiritWatcher/1.0 (personal desktop app)"}
    )

    with urlopen(request, timeout=15) as response:
        data = json.load(response)

    prices = {}

    # The API normally keys results by item name. The fallback loop also
    # accepts responses keyed by item ID if they contain a name field.
    for name in names:
        entry = data.get(name) if isinstance(data, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("price"), (int, float)):
            prices[name] = int(entry["price"])

    if isinstance(data, dict):
        for entry in data.values():
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            price = entry.get("price")
            if name in names and isinstance(price, (int, float)):
                prices[name] = int(price)

    return prices

# ===== GUI APP =====

class SerenWatcherGUI:
    def __init__(self, root):
        self.root = root
        self.first_run_setup = not CONFIG_PATH.exists()
        self.root.title("Seren Spirit Watcher")
        self.window_icon_image = None
        self.title_icon_image = None
        self.native_icon_handles = {}
        self.apply_window_icon()
        self.root.geometry("1200x700")
        self.root.minsize(800, 500)
        self.root.overrideredirect(True)

        self.is_maximized = False
        self.restore_geometry = None
        self.drag_start_x = 0
        self.drag_start_y = 0
        self.resize_direction = None
        self.resize_start = None

        self.running = False
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.ui_queue = queue.Queue()
        self.ge_prices = {}
        self.price_refreshing = False

        self.recent_valid_timestamps = deque(maxlen=6)
        self.recent_catalyst_timestamps = deque(maxlen=6)
        self.recent_misc_timestamps = deque(maxlen=6)
        self.pending_review_keys = set()
        self.review_windows = {}

        initialize_database()
        self.build_gui()
        self.reload_tables()

        self.root.after(100, self.process_ui_queue)
        self.root.after(200, self.refresh_prices)
        self.root.after(50, self.ensure_taskbar_presence)
        if self.first_run_setup:
            self.root.after(350, lambda: self.show_setup_dialog(required=True))
        self.root.bind("<Map>", self.on_window_mapped)

    def build_gui(self):
        style = ttk.Style()
        style.theme_use("clam")

        self.root.configure(background=COLOR_BG)

        table_font = tkfont.Font(family="Segoe UI", size=10)
        heading_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

        style.configure("TFrame", background=COLOR_BG)
        style.configure(
            "TLabel",
            background=COLOR_BG,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 10)
        )
        style.configure(
            "Status.TLabel",
            background=COLOR_BG,
            foreground=COLOR_GREEN,
            font=("Segoe UI", 10)
        )
        style.configure(
            "TLabelframe",
            background=COLOR_PANEL,
            bordercolor=COLOR_GOLD_DARK,
            lightcolor=COLOR_GOLD_DARK,
            darkcolor=COLOR_GOLD_DARK,
            relief="solid",
            borderwidth=1
        )
        style.configure(
            "TLabelframe.Label",
            background=COLOR_BG,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 10, "bold")
        )
        style.configure(
            "Panel.TLabel",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 11, "bold")
        )

        style.configure(
            "TButton",
            background=COLOR_PANEL,
            foreground=COLOR_GOLD,
            bordercolor=COLOR_GOLD_DARK,
            lightcolor=COLOR_GOLD_DARK,
            darkcolor=COLOR_GOLD_DARK,
            font=("Segoe UI", 10),
            padding=(12, 5)
        )
        style.map(
            "TButton",
            background=[("active", "#2b2417"), ("pressed", COLOR_SELECTION)],
            foreground=[("disabled", COLOR_MUTED), ("active", "#f2c85b")]
        )
        style.configure("Start.TButton", foreground=COLOR_GREEN)
        style.configure("Stop.TButton", foreground=COLOR_RED)
        style.configure(
            "TNotebook",
            background=COLOR_BG,
            bordercolor=COLOR_GOLD_DARK,
            tabmargins=(2, 2, 2, 0)
        )
        style.configure(
            "TNotebook.Tab",
            background="#18140d",
            foreground=COLOR_MUTED,
            bordercolor=COLOR_GOLD_DARK,
            lightcolor=COLOR_GOLD_DARK,
            darkcolor=COLOR_GOLD_DARK,
            font=("Segoe UI", 10, "bold"),
            padding=(18, 7)
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_PANEL), ("active", "#2b2417")],
            foreground=[("selected", COLOR_GOLD), ("active", "#f2c85b")]
        )

        style.configure(
            "Treeview",
            background=COLOR_PANEL,
            fieldbackground=COLOR_PANEL,
            foreground=COLOR_TEXT,
            bordercolor=COLOR_GOLD_DARK,
            lightcolor=COLOR_GOLD_DARK,
            darkcolor=COLOR_GOLD_DARK,
            font=table_font,
            rowheight=24
        )
        style.map(
            "Treeview",
            background=[("selected", COLOR_SELECTION)],
            foreground=[("selected", "#fff1bf")]
        )
        style.configure(
            "Treeview.Heading",
            background="#18140d",
            foreground=COLOR_GOLD,
            bordercolor=COLOR_GOLD_DARK,
            lightcolor=COLOR_GOLD_DARK,
            darkcolor=COLOR_GOLD_DARK,
            font=heading_font,
            relief="flat"
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#2b2417")]
        )
        style.configure(
            "Vertical.TScrollbar",
            background="#2b2417",
            troughcolor=COLOR_PANEL,
            bordercolor=COLOR_GOLD_DARK,
            arrowcolor=COLOR_GOLD
        )

        self.window_frame = tk.Frame(
            self.root,
            background=COLOR_GOLD_DARK,
            highlightbackground=COLOR_GOLD,
            highlightcolor=COLOR_GOLD,
            highlightthickness=1,
            borderwidth=0
        )
        self.window_frame.pack(fill="both", expand=True)

        self.build_title_bar()

        main = ttk.Frame(self.window_frame, padding=8)
        main.pack(fill="both", expand=True)

        # ----- Top controls -----

        controls = ttk.Frame(main)
        controls.pack(fill="x", pady=(0, 8))

        self.start_button = ttk.Button(
            controls,
            text="Start",
            command=self.start,
            style="Start.TButton",
            state="disabled" if self.first_run_setup else "normal"
        )
        self.start_button.pack(side="left", padx=(0, 5))

        self.stop_button = ttk.Button(
            controls,
            text="Stop",
            command=self.stop,
            state="disabled",
            style="Stop.TButton"
        )
        self.stop_button.pack(side="left", padx=5)

        ttk.Button(controls, text="Refresh Prices", command=self.refresh_prices).pack(side="left", padx=5)
        ttk.Button(controls, text="Setup", command=self.show_setup_dialog).pack(side="left", padx=5)
        ttk.Button(controls, text="Reload App", command=self.reload_app).pack(side="left", padx=5)
        ttk.Button(controls, text="Quit", command=self.quit).pack(side="left", padx=5)

        self.status_var = tk.StringVar(value="Stopped")
        ttk.Label(
            controls,
            textvariable=self.status_var,
            style="Status.TLabel"
        ).pack(side="left", padx=20)

        # ----- Tracking tabs -----

        self.notebook = ttk.Notebook(main)
        self.notebook.pack(fill="both", expand=True)

        self.seren_tab = ttk.Frame(self.notebook, padding=(6, 8, 6, 6))
        self.catalyst_tab = ttk.Frame(self.notebook, padding=(6, 8, 6, 6))
        self.misc_tab = ttk.Frame(self.notebook, padding=(6, 8, 6, 6))
        self.notebook.add(self.seren_tab, text="Seren Spirits")
        self.notebook.add(self.catalyst_tab, text="Catalysts")
        self.notebook.add(self.misc_tab, text="Misc.")

        body = ttk.Frame(self.seren_tab)
        body.pack(fill="both", expand=True)

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        self.total_frame = ttk.LabelFrame(body, text="Total Quantity by Item", padding=6)
        self.total_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))

        self.drop_frame = ttk.LabelFrame(body, text="Drop Count / Quantity Range", padding=6)
        self.drop_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))

        self.build_total_table()
        self.build_drop_table()

        self.summary_frame = ttk.LabelFrame(self.seren_tab, text="Summary", padding=6)
        self.summary_frame.pack(fill="x", pady=(8, 0))
        self.build_summary_panel()

        self.build_catalyst_tab()
        self.build_misc_tab()

        self.create_resize_grips()

    # ===== Custom window chrome =====

    def apply_window_icon(self):
        """Apply the Seren spirit artwork to Tk and native Windows surfaces."""
        try:
            if ICON_PNG_PATH.exists():
                self.window_icon_image = tk.PhotoImage(file=str(ICON_PNG_PATH))
                self.root.iconphoto(True, self.window_icon_image)

            if ICON_PATH.exists():
                self.root.iconbitmap(default=str(ICON_PATH))
        except tk.TclError:
            # The tracker should remain usable if an icon asset is moved.
            pass

    def build_title_bar(self):
        title_bg = "#11100c"

        self.title_bar = tk.Frame(
            self.window_frame,
            background=title_bg,
            height=38
        )
        self.title_bar.pack(fill="x", padx=1, pady=(1, 0))
        self.title_bar.pack_propagate(False)

        title_widgets = [self.title_bar]

        if TITLE_ICON_PATH.exists():
            try:
                self.title_icon_image = tk.PhotoImage(file=str(TITLE_ICON_PATH))
                title_icon = tk.Label(
                    self.title_bar,
                    image=self.title_icon_image,
                    background=title_bg,
                    borderwidth=0
                )
                title_icon.pack(side="left", padx=(10, 5))
                title_widgets.append(title_icon)
            except tk.TclError:
                self.title_icon_image = None

        title = tk.Label(
            self.title_bar,
            text="Seren Spirit Watcher",
            background=title_bg,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 11, "bold")
        )
        title.pack(side="left", padx=(0 if self.title_icon_image else 10, 0))
        title_widgets.append(title)

        controls = tk.Frame(self.title_bar, background=title_bg)
        controls.pack(side="right", fill="y")

        self.minimize_button = self.make_title_button(
            controls, "—", self.minimize_window
        )
        self.maximize_button = self.make_title_button(
            controls, "□", self.toggle_maximize
        )
        self.close_button = self.make_title_button(
            controls, "×", self.quit, close_button=True
        )

        for widget in title_widgets:
            widget.bind("<ButtonPress-1>", self.start_window_drag)
            widget.bind("<B1-Motion>", self.drag_window)
            widget.bind("<Double-Button-1>", self.toggle_maximize)

        tk.Frame(
            self.window_frame,
            background=COLOR_GOLD_DARK,
            height=1
        ).pack(fill="x")

    def make_title_button(self, parent, text, command, close_button=False):
        button = tk.Label(
            parent,
            text=text,
            background="#11100c",
            foreground=COLOR_GOLD,
            font=("Segoe UI", 13),
            width=4,
            cursor="hand2"
        )
        button.pack(side="left", fill="y")
        button.bind("<Button-1>", lambda _event: command())

        hover_color = "#7d241d" if close_button else "#332817"
        button.bind("<Enter>", lambda _event: button.configure(background=hover_color))
        button.bind("<Leave>", lambda _event: button.configure(background="#11100c"))
        return button

    def start_window_drag(self, event):
        if self.is_maximized:
            return
        self.drag_start_x = event.x_root - self.root.winfo_x()
        self.drag_start_y = event.y_root - self.root.winfo_y()

    def drag_window(self, event):
        if self.is_maximized:
            return
        x = event.x_root - self.drag_start_x
        y = event.y_root - self.drag_start_y
        self.root.geometry(f"+{x}+{y}")

    def minimize_window(self):
        self.root.overrideredirect(False)
        self.root.iconify()

    def on_window_mapped(self, _event=None):
        if self.root.state() == "normal" and not self.root.overrideredirect():
            self.root.after(10, self.restore_custom_chrome)

    def restore_custom_chrome(self):
        self.root.overrideredirect(True)
        self.ensure_taskbar_presence()

    def toggle_maximize(self, _event=None):
        if self.is_maximized:
            if self.restore_geometry:
                self.root.geometry(self.restore_geometry)
            self.is_maximized = False
            self.maximize_button.configure(text="□")
            return

        self.restore_geometry = self.root.geometry()
        left, top, right, bottom = self.get_monitor_work_area()
        self.root.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
        self.is_maximized = True
        self.maximize_button.configure(text="❐")

    def get_monitor_work_area(self):
        try:
            import ctypes
            from ctypes import wintypes

            class MonitorInfo(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            hwnd = self.get_window_handle()
            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            work = info.rcWork
            return work.left, work.top, work.right, work.bottom
        except Exception:
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def get_window_handle(self):
        import ctypes

        self.root.update_idletasks()
        child_handle = self.root.winfo_id()
        # Tk creates a wrapper HWND around the widget HWND. GetAncestor with
        # GA_ROOT reliably finds the actual top-level window Windows manages.
        return ctypes.windll.user32.GetAncestor(child_handle, 2) or child_handle

    def ensure_taskbar_presence(self):
        try:
            import ctypes

            hwnd = self.get_window_handle()
            extended_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            extended_style &= ~0x00000080  # WS_EX_TOOLWINDOW
            extended_style |= 0x00040000   # WS_EX_APPWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, extended_style)

            # Tell Windows the non-client/app-window metadata changed.
            ctypes.windll.user32.SetWindowPos(
                hwnd,
                0,
                0,
                0,
                0,
                0,
                0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
            )

            self.apply_native_taskbar_icon(hwnd)

            # Borderless Tk windows sometimes need to be re-registered after
            # WS_EX_APPWINDOW is applied before their taskbar icon appears.
            self.root.withdraw()
            self.root.after(20, self.show_after_taskbar_refresh)
        except Exception:
            pass

    def apply_native_taskbar_icon(self, hwnd):
        """Set both Windows icon sizes on the borderless top-level window."""
        if not ICON_PATH.exists():
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE

        if not self.native_icon_handles:
            load_from_file = 0x0010
            image_icon = 1
            self.native_icon_handles["small"] = user32.LoadImageW(
                None, str(ICON_PATH), image_icon, 16, 16, load_from_file
            )
            self.native_icon_handles["big"] = user32.LoadImageW(
                None, str(ICON_PATH), image_icon, 32, 32, load_from_file
            )

        wm_seticon = 0x0080
        icon_small = 0
        icon_big = 1
        user32.SendMessageW(
            hwnd, wm_seticon, icon_small, self.native_icon_handles["small"]
        )
        user32.SendMessageW(
            hwnd, wm_seticon, icon_big, self.native_icon_handles["big"]
        )

    def flash_taskbar_icon(self):
        """Flash the Windows taskbar button until the app receives attention."""
        try:
            import ctypes
            from ctypes import wintypes

            class FlashWindowInfo(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]

            info = FlashWindowInfo(
                ctypes.sizeof(FlashWindowInfo),
                self.get_window_handle(),
                0x00000003 | 0x0000000C,  # FLASHW_ALL | FLASHW_TIMERNOFG
                5,
                0,
            )
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            self.root.bell()

    def show_after_taskbar_refresh(self):
        self.root.deiconify()
        self.root.lift()

    def create_resize_grips(self):
        grips = (
            ("n", "sb_v_double_arrow", {"x": 6, "y": 0, "relwidth": 1, "width": -12, "height": 5}),
            ("s", "sb_v_double_arrow", {"x": 6, "rely": 1, "y": -5, "relwidth": 1, "width": -12, "height": 5}),
            ("w", "sb_h_double_arrow", {"x": 0, "y": 6, "width": 5, "relheight": 1, "height": -12}),
            ("e", "sb_h_double_arrow", {"relx": 1, "x": -5, "y": 6, "width": 5, "relheight": 1, "height": -12}),
            ("nw", "size_nw_se", {"x": 0, "y": 0, "width": 8, "height": 8}),
            ("ne", "size_ne_sw", {"relx": 1, "x": -8, "y": 0, "width": 8, "height": 8}),
            ("sw", "size_ne_sw", {"x": 0, "rely": 1, "y": -8, "width": 8, "height": 8}),
            ("se", "size_nw_se", {"relx": 1, "x": -8, "rely": 1, "y": -8, "width": 8, "height": 8}),
        )

        self.resize_grips = []
        for direction, cursor, placement in grips:
            grip = tk.Frame(self.window_frame, cursor=cursor, background=COLOR_GOLD_DARK)
            grip.place(**placement)
            grip.bind(
                "<ButtonPress-1>",
                lambda event, current_direction=direction: self.start_resize(
                    event, current_direction
                )
            )
            grip.bind("<B1-Motion>", self.resize_window)
            self.resize_grips.append(grip)

        self.root.after_idle(self.raise_resize_grips)

    def raise_resize_grips(self):
        for grip in self.resize_grips:
            grip.lift()

    def start_resize(self, event, direction):
        if self.is_maximized:
            return
        self.resize_direction = direction
        self.resize_start = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_width(),
            self.root.winfo_height(),
        )

    def resize_window(self, event):
        if self.is_maximized or not self.resize_start:
            return

        start_x, start_y, window_x, window_y, width, height = self.resize_start
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        direction = self.resize_direction

        min_width, min_height = 800, 500
        new_x, new_y = window_x, window_y
        new_width, new_height = width, height

        if "e" in direction:
            new_width = max(min_width, width + dx)
        if "s" in direction:
            new_height = max(min_height, height + dy)
        if "w" in direction:
            new_width = max(min_width, width - dx)
            new_x = window_x + width - new_width
        if "n" in direction:
            new_height = max(min_height, height - dy)
            new_y = window_y + height - new_height

        self.root.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")

    def build_total_table(self):
        self.total_frame.rowconfigure(0, weight=1)
        self.total_frame.columnconfigure(0, weight=3)
        self.total_frame.columnconfigure(1, weight=2)

        self.total_table = ttk.Treeview(
            self.total_frame,
            columns=("display",),
            show="headings"
        )
        self.total_table.heading("display", text="Total")
        self.total_table.column("display", width=300, anchor="w")

        self.total_value_table = ttk.Treeview(
            self.total_frame,
            columns=("ge_value",),
            show="headings",
            selectmode="none"
        )
        self.total_value_table.heading("ge_value", text="Approx. GE Value")
        self.total_value_table.column("ge_value", width=180, anchor="w")

        self.total_scrollbar = ttk.Scrollbar(
            self.total_frame,
            orient="vertical",
            command=self.scroll_total_tables
        )
        self.total_table.configure(yscrollcommand=self.on_total_table_scroll)
        self.total_value_table.configure(yscrollcommand=self.on_value_table_scroll)

        self.total_table.tag_configure("even", background=COLOR_PANEL)
        self.total_table.tag_configure("odd", background=COLOR_ROW_ALT)

        for parity, background in (("even", COLOR_PANEL), ("odd", COLOR_ROW_ALT)):
            self.total_value_table.tag_configure(
                f"{parity}_default",
                background=background,
                foreground=COLOR_TEXT
            )
            self.total_value_table.tag_configure(
                f"{parity}_million",
                background=background,
                foreground=COLOR_PRICE_MILLION
            )
            self.total_value_table.tag_configure(
                f"{parity}_billion",
                background=background,
                foreground=COLOR_PRICE_BILLION
            )

        self.total_table.grid(row=0, column=0, sticky="nsew")
        self.total_value_table.grid(row=0, column=1, sticky="nsew")
        self.total_scrollbar.grid(row=0, column=2, sticky="ns")

        self.total_table.bind("<MouseWheel>", self.on_total_mousewheel)
        self.total_value_table.bind("<MouseWheel>", self.on_total_mousewheel)

    def scroll_total_tables(self, *args):
        self.total_table.yview(*args)
        self.total_value_table.yview(*args)

    def on_total_table_scroll(self, first, last):
        self.total_scrollbar.set(first, last)
        if not getattr(self, "_syncing_total_tables", False):
            self._syncing_total_tables = True
            self.total_value_table.yview_moveto(first)
            self._syncing_total_tables = False

    def on_value_table_scroll(self, first, last):
        self.total_scrollbar.set(first, last)
        if not getattr(self, "_syncing_total_tables", False):
            self._syncing_total_tables = True
            self.total_table.yview_moveto(first)
            self._syncing_total_tables = False

    def on_total_mousewheel(self, event):
        steps = -int(event.delta / 120) if event.delta else 0
        if steps:
            self.total_table.yview_scroll(steps, "units")
            self.total_value_table.yview_scroll(steps, "units")
        return "break"

    def build_drop_table(self):
        self.drop_frame.rowconfigure(0, weight=1)
        self.drop_frame.columnconfigure(0, weight=1)

        columns = ("display", "odds")

        self.drop_table = ttk.Treeview(
            self.drop_frame,
            columns=columns,
            show="headings"
        )

        self.drop_table.heading("display", text="Drops")
        self.drop_table.heading("odds", text="Odds")

        self.drop_table.column("display", width=300, anchor="w")
        self.drop_table.column("odds", width=180, anchor="w")

        scrollbar = ttk.Scrollbar(self.drop_frame, orient="vertical", command=self.drop_table.yview)
        self.drop_table.configure(yscrollcommand=scrollbar.set)
        self.drop_table.tag_configure("even", background=COLOR_PANEL)
        self.drop_table.tag_configure("odd", background=COLOR_ROW_ALT)

        self.drop_table.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

    def build_summary_panel(self):
        self.recent_drops_var = tk.StringVar(value="Recent drops: None")
        self.total_drops_var = tk.StringVar(value="Total drops obtained: 0")
        self.hsr_chance_var = tk.StringVar(value="Chance of HSR: 0.0000%")
        self.total_ge_value_var = tk.StringVar(value="Approx. total GE value: 0")

        for row_index, variable in enumerate((
            self.recent_drops_var,
            self.total_drops_var,
            self.hsr_chance_var,
            self.total_ge_value_var,
        )):
            ttk.Label(
                self.summary_frame,
                textvariable=variable,
                style="Panel.TLabel"
            ).grid(row=row_index, column=0, sticky="w", pady=1)

    def build_catalyst_tab(self):
        body = ttk.Frame(self.catalyst_tab)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        total_frame = ttk.LabelFrame(
            body,
            text="Total Quantity by Item",
            padding=6
        )
        total_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        total_frame.rowconfigure(0, weight=1)
        total_frame.columnconfigure(0, weight=1)

        self.catalyst_total_table = ttk.Treeview(
            total_frame,
            columns=("display",),
            show="headings"
        )
        self.catalyst_total_table.heading("display", text="Total")
        self.catalyst_total_table.column("display", width=420, anchor="w")
        total_scrollbar = ttk.Scrollbar(
            total_frame,
            orient="vertical",
            command=self.catalyst_total_table.yview
        )
        self.catalyst_total_table.configure(yscrollcommand=total_scrollbar.set)
        self.catalyst_total_table.grid(row=0, column=0, sticky="nsew")
        total_scrollbar.grid(row=0, column=1, sticky="ns")

        rate_frame = ttk.LabelFrame(
            body,
            text="Drop Count / Observed Rate",
            padding=6
        )
        rate_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        rate_frame.rowconfigure(0, weight=1)
        rate_frame.columnconfigure(0, weight=3)
        rate_frame.columnconfigure(1, weight=2)

        self.catalyst_rate_table = ttk.Treeview(
            rate_frame,
            columns=("display", "rate"),
            show="headings"
        )
        self.catalyst_rate_table.heading("display", text="Drops")
        self.catalyst_rate_table.heading("rate", text="Observed Rate")
        self.catalyst_rate_table.column("display", width=300, anchor="w")
        self.catalyst_rate_table.column("rate", width=180, anchor="w")
        rate_scrollbar = ttk.Scrollbar(
            rate_frame,
            orient="vertical",
            command=self.catalyst_rate_table.yview
        )
        self.catalyst_rate_table.configure(yscrollcommand=rate_scrollbar.set)
        self.catalyst_rate_table.grid(row=0, column=0, columnspan=2, sticky="nsew")
        rate_scrollbar.grid(row=0, column=2, sticky="ns")

        for table in (self.catalyst_total_table, self.catalyst_rate_table):
            table.tag_configure("even", background=COLOR_PANEL)
            table.tag_configure("odd", background=COLOR_ROW_ALT)

        summary = ttk.LabelFrame(self.catalyst_tab, text="Summary", padding=6)
        summary.pack(fill="x", pady=(8, 0))
        self.catalyst_recent_var = tk.StringVar(value="Recent catalyst drops: None")
        self.catalyst_total_events_var = tk.StringVar(
            value="Total catalyst drops obtained: 0"
        )
        ttk.Label(
            summary,
            textvariable=self.catalyst_recent_var,
            style="Panel.TLabel"
        ).pack(anchor="w", pady=1)
        ttk.Label(
            summary,
            textvariable=self.catalyst_total_events_var,
            style="Panel.TLabel"
        ).pack(anchor="w", pady=1)

    def build_misc_tab(self):
        counters = ttk.LabelFrame(self.misc_tab, text="Counters", padding=12)
        counters.pack(fill="x")

        self.birds_nests_var = tk.StringVar(value="Bird's Nests Found: 0")
        ttk.Label(
            counters,
            textvariable=self.birds_nests_var,
            style="Panel.TLabel",
            font=("Segoe UI", 12, "bold")
        ).pack(anchor="w")

    # ===== Capture setup =====

    def show_setup_dialog(self, required=False):
        if hasattr(self, "setup_window") and self.setup_window.winfo_exists():
            self.setup_window.lift()
            self.setup_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self.setup_window = window
        window.title("Seren Spirit Tracker Setup")
        window.geometry("760x650")
        window.minsize(680, 580)
        window.configure(background=COLOR_BG)
        window.transient(self.root)
        window.grab_set()
        if self.window_icon_image is not None:
            window.iconphoto(True, self.window_icon_image)

        container = ttk.Frame(window, padding=16)
        container.pack(fill="both", expand=True)
        ttk.Label(
            container,
            text="Chat Capture Setup",
            foreground=COLOR_GOLD,
            font=("Segoe UI", 15, "bold")
        ).pack(anchor="w")
        ttk.Label(
            container,
            text=(
                "Choose the monitor containing RuneScape, then adjust the capture "
                "rectangle until the preview contains the chat messages. Coordinates "
                "are relative to the selected monitor."
            ),
            wraplength=700
        ).pack(anchor="w", pady=(4, 12))

        with mss.mss() as sct:
            monitors = list(sct.monitors)

        monitor_options = {}
        for index, monitor in enumerate(monitors[1:], start=1):
            label = (
                f"Monitor {index} — {monitor['width']}x{monitor['height']} "
                f"at ({monitor['left']}, {monitor['top']})"
            )
            monitor_options[label] = index

        selected_monitor = MONITOR_NUMBER
        if selected_monitor not in monitor_options.values():
            selected_monitor = 1
        selected_label = next(
            label for label, index in monitor_options.items()
            if index == selected_monitor
        )

        form = ttk.Frame(container)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        monitor_var = tk.StringVar(value=selected_label)
        left_var = tk.StringVar(value=str(REGION_LEFT_OFFSET))
        width_var = tk.StringVar(value=str(REGION_WIDTH))
        height_var = tk.StringVar(value=str(REGION_HEIGHT))
        bottom_var = tk.StringVar(
            value=str(max(0, REGION_BOTTOM_OFFSET - REGION_HEIGHT))
        )
        interval_var = tk.StringVar(value=str(CHECK_EVERY_SECONDS))

        ttk.Label(form, text="Monitor").grid(row=0, column=0, sticky="w", pady=3)
        monitor_box = ttk.Combobox(
            form,
            textvariable=monitor_var,
            values=list(monitor_options),
            state="readonly"
        )
        monitor_box.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(10, 0), pady=3)

        fields = (
            ("Width", width_var, "capture width in pixels"),
            ("Height", height_var, "capture height in pixels"),
            ("Left offset", left_var, "pixels from the monitor's left edge"),
            ("Bottom offset", bottom_var, "pixels from the monitor's bottom edge"),
            ("Check interval", interval_var, "seconds between OCR checks"),
        )
        for row, (label, variable, hint) in enumerate(fields, start=1):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Entry(form, textvariable=variable, width=12).grid(
                row=row, column=1, sticky="w", padx=(10, 8), pady=3
            )
            ttk.Label(form, text=hint, foreground=COLOR_MUTED).grid(
                row=row, column=2, sticky="w", pady=3
            )

        preview_frame = ttk.LabelFrame(container, text="Capture Preview", padding=6)
        preview_frame.pack(fill="both", expand=True, pady=(14, 10))
        preview_label = ttk.Label(
            preview_frame,
            text="Select Preview Capture to verify the chatbox area.",
            anchor="center"
        )
        preview_label.pack(fill="both", expand=True)

        def read_form():
            try:
                region_height = int(height_var.get())
                bottom_edge_offset = int(bottom_var.get())
                settings = {
                    "monitor_number": monitor_options[monitor_var.get()],
                    "region_left_offset": int(left_var.get()),
                    "region_width": int(width_var.get()),
                    "region_height": region_height,
                    "region_bottom_offset": region_height + bottom_edge_offset,
                    "check_every_seconds": float(interval_var.get()),
                }
            except (KeyError, ValueError):
                raise ValueError("Enter valid numbers in every setup field.")

            monitor = monitors[settings["monitor_number"]]
            if settings["region_left_offset"] < 0:
                raise ValueError("Left offset cannot be negative.")
            if settings["region_width"] <= 0 or settings["region_height"] <= 0:
                raise ValueError("Width and height must be greater than zero.")
            if settings["check_every_seconds"] < 0.1:
                raise ValueError("Check interval must be at least 0.1 seconds.")
            if bottom_edge_offset < 0:
                raise ValueError("Bottom offset cannot be negative.")
            if settings["region_left_offset"] + settings["region_width"] > monitor["width"]:
                raise ValueError("The capture extends past the monitor's right edge.")
            if settings["region_bottom_offset"] > monitor["height"]:
                raise ValueError(
                    "Height plus bottom offset cannot exceed the monitor height."
                )
            return settings

        def preview_capture():
            try:
                settings = read_form()
                monitor = monitors[settings["monitor_number"]]
                region = {
                    "left": monitor["left"] + settings["region_left_offset"],
                    "top": (
                        monitor["top"] + monitor["height"]
                        - settings["region_bottom_offset"]
                    ),
                    "width": settings["region_width"],
                    "height": settings["region_height"],
                }
                with mss.mss() as sct:
                    shot = sct.grab(region)
                image = Image.frombytes("RGB", shot.size, shot.rgb)
                image.thumbnail((700, 320), Image.Resampling.LANCZOS)
                window.preview_image = ImageTk.PhotoImage(image)
                preview_label.configure(image=window.preview_image, text="")
            except Exception as exc:
                messagebox.showerror("Preview failed", str(exc), parent=window)

        def save_setup():
            try:
                settings = read_form()
                temporary_path = CONFIG_PATH.with_suffix(".json.tmp")
                temporary_path.write_text(
                    json.dumps(settings, indent=2) + "\n",
                    encoding="utf-8"
                )
                temporary_path.replace(CONFIG_PATH)
                apply_settings(settings)
                RUNTIME_SETTINGS.update(settings)
                self.first_run_setup = False
                self.start_button.configure(state="normal")
                self.status_var.set("Setup saved — ready to start")
                window.destroy()
            except Exception as exc:
                messagebox.showerror("Could not save setup", str(exc), parent=window)

        buttons = ttk.Frame(container)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Preview Capture", command=preview_capture).pack(side="left")
        ttk.Button(buttons, text="Save Setup", command=save_setup).pack(side="right")
        if not required:
            ttk.Button(buttons, text="Cancel", command=window.destroy).pack(
                side="right", padx=(0, 8)
            )

        if required:
            window.protocol("WM_DELETE_WINDOW", self.quit)

        window.focus_force()

    # ===== Table updating =====

    def clear_table(self, table):
        for item in table.get_children():
            table.delete(item)

    def reload_tables(self):
        rows = read_csv_rows()

        self.clear_table(self.total_table)
        self.clear_table(self.total_value_table)
        self.clear_table(self.drop_table)

        if rows:
            recent_drops = [
                f"{row['quantity']} x {row['reward']}"
                for row in reversed(rows[-3:])
            ]
            self.recent_drops_var.set(
                f"Recent drops: {' | '.join(recent_drops)}"
            )
        else:
            self.recent_drops_var.set("Recent drops: None")

        # Left: total quantity by item
        quantity_totals = defaultdict(int)

        # Right: count/min/max by item
        drop_stats = {}

        for row in rows:
            raw_reward = row["reward"]
            quantity = row["quantity"]

            internal_key, display_reward = get_summary_keys(raw_reward, quantity)

            quantity_totals[internal_key] += quantity

            if internal_key not in drop_stats:
                drop_stats[internal_key] = {
                    "display_reward": display_reward,
                    "count": 0,
                    "min": quantity,
                    "max": quantity,
                }

            drop_stats[internal_key]["count"] += 1
            drop_stats[internal_key]["min"] = min(drop_stats[internal_key]["min"], quantity)
            drop_stats[internal_key]["max"] = max(drop_stats[internal_key]["max"], quantity)

        total_width = max(
            (len(str(total)) for total in quantity_totals.values()),
            default=1
        )

        for row_index, internal_key in enumerate(
            sorted(quantity_totals.keys(), key=str.lower)
        ):
            total = quantity_totals[internal_key]
            aligned_total = str(total).rjust(total_width, "\u2007")

            display_reward = drop_stats[internal_key]["display_reward"]

            unit_price = self.ge_prices.get(display_reward)
            numeric_ge_value = total * unit_price if unit_price is not None else None
            ge_value = format_gp(numeric_ge_value) if numeric_ge_value is not None else "—"
            parity = "even" if row_index % 2 == 0 else "odd"

            if numeric_ge_value is not None and numeric_ge_value > 1_000_000_000:
                price_tier = "billion"
            elif numeric_ge_value is not None and numeric_ge_value > 1_000_000:
                price_tier = "million"
            else:
                price_tier = "default"

            self.total_table.insert(
                "",
                "end",
                values=(f"{aligned_total} x {display_reward}",),
                tags=(parity,)
            )
            self.total_value_table.insert(
                "",
                "end",
                values=(ge_value,),
                tags=(f"{parity}_{price_tier}",)
            )

        total_ge_value = sum(
            row["quantity"] * self.ge_prices.get(row["reward"], 0)
            for row in rows
        )
        self.total_ge_value_var.set(
            f"Approx. total GE value: {format_gp(total_ge_value)}"
        )

        total_drops = sum(stats["count"] for stats in drop_stats.values())
        hsr_chance = 1 - ((1 - 1 / 83200) ** total_drops)
        hsr_chance_percent = hsr_chance * 100

        self.total_drops_var.set(
            f"Total drops obtained: {total_drops}"
        )
        self.hsr_chance_var.set(
            f"Chance of HSR: {hsr_chance_percent:.4f}%"
        )

        drop_rows = []
        count_width = max(
            (len(str(stats["count"])) for stats in drop_stats.values()),
            default=1
        )

        for internal_key, stats in drop_stats.items():
            display_reward = stats["display_reward"]
            count = stats["count"]
            aligned_count = str(count).rjust(count_width, "\u2007")
            min_qty = stats["min"]
            max_qty = stats["max"]

            if min_qty == max_qty:
                qty_range = str(min_qty)
            else:
                qty_range = f"{min_qty}-{max_qty}"

            display = f"{aligned_count} x {qty_range} {display_reward}"

            if total_drops > 0:
                odds_n = (count / total_drops) * 83200
            else:
                odds_n = 0

            odds_display = f"{odds_n:.1f}/83200"

            drop_rows.append({
                "internal_key": internal_key,
                "display_reward": display_reward,
                "count": count,
                "odds_n": odds_n,
                "display": display,
                "odds_display": odds_display,
            })

        # Rarest first by odds_n, then alphabetical by reward
        drop_rows.sort(
            key=lambda row: (
                row["odds_n"],
                row["display_reward"].lower(),
                row["internal_key"].lower()
            )
        )

        for row_index, row in enumerate(drop_rows):
            self.drop_table.insert(
                "",
                "end",
                values=(row["display"], row["odds_display"]),
                tags=("even" if row_index % 2 == 0 else "odd",)
            )

        catalyst_count = self.reload_catalyst_tables()
        self.reload_misc_tab()
        self.status_var.set(
            f"Loaded {len(rows)} Seren drops and {catalyst_count} catalyst drops"
        )

    def reload_catalyst_tables(self):
        rows = read_catalyst_rows()
        self.clear_table(self.catalyst_total_table)
        self.clear_table(self.catalyst_rate_table)

        if rows:
            recent = [
                f"{row['quantity']} x {row['item']}"
                for row in reversed(rows[-3:])
            ]
            self.catalyst_recent_var.set(
                f"Recent catalyst drops: {' | '.join(recent)}"
            )
        else:
            self.catalyst_recent_var.set("Recent catalyst drops: None")

        stats = {}
        for row in rows:
            item = row["item"]
            key = item.casefold()
            if key not in stats:
                stats[key] = {
                    "item": item,
                    "quantity": 0,
                    "count": 0,
                    "min": row["quantity"],
                    "max": row["quantity"],
                }
            stats[key]["quantity"] += row["quantity"]
            stats[key]["count"] += 1
            stats[key]["min"] = min(stats[key]["min"], row["quantity"])
            stats[key]["max"] = max(stats[key]["max"], row["quantity"])

        quantity_width = max(
            (len(str(entry["quantity"])) for entry in stats.values()),
            default=1
        )
        for row_index, entry in enumerate(
            sorted(
                stats.values(),
                key=lambda value: catalyst_item_sort_key(value["item"])
            )
        ):
            aligned_quantity = str(entry["quantity"]).rjust(
                quantity_width, "\u2007"
            )
            self.catalyst_total_table.insert(
                "",
                "end",
                values=(f"{aligned_quantity} x {entry['item']}",),
                tags=("even" if row_index % 2 == 0 else "odd",)
            )

        total_events = len(rows)
        self.catalyst_total_events_var.set(
            f"Total catalyst drops obtained: {total_events}"
        )
        count_width = max(
            (len(str(entry["count"])) for entry in stats.values()),
            default=1
        )
        rate_rows = sorted(
            stats.values(),
            key=lambda value: catalyst_item_sort_key(value["item"])
        )
        for row_index, entry in enumerate(rate_rows):
            count = entry["count"]
            aligned_count = str(count).rjust(count_width, "\u2007")
            min_quantity = entry["min"]
            max_quantity = entry["max"]
            quantity_range = (
                str(min_quantity)
                if min_quantity == max_quantity
                else f"{min_quantity}-{max_quantity}"
            )
            rate = (count / total_events * 100) if total_events else 0
            self.catalyst_rate_table.insert(
                "",
                "end",
                values=(
                    f"{aligned_count} x {quantity_range} {entry['item']}",
                    f"{rate:.2f}% ({count}/{total_events})",
                ),
                tags=("even" if row_index % 2 == 0 else "odd",)
            )

        return total_events

    def reload_misc_tab(self):
        birds_nests = read_misc_counter("birds_nests")
        self.birds_nests_var.set(f"Bird's Nests Found: {birds_nests}")

    # ===== GE prices =====

    def refresh_prices(self):
        if self.price_refreshing:
            return

        item_names = {row["reward"] for row in read_csv_rows()}
        if not item_names:
            self.ge_prices = {}
            self.reload_tables()
            return

        self.price_refreshing = True
        self.status_var.set("Refreshing GE prices...")

        threading.Thread(
            target=self._price_worker,
            args=(item_names,),
            daemon=True
        ).start()

    def _price_worker(self, item_names):
        try:
            prices = fetch_ge_prices(item_names)
            self.ui_queue.put(("prices", prices, len(item_names)))
        except Exception as exc:
            self.ui_queue.put(("price_error", str(exc)))

    # ===== Watcher controls =====

    def start(self):
        if self.running:
            return

        if not TESSERACT_PATH:
            messagebox.showerror(
                "OCR engine unavailable",
                "Tesseract OCR could not be found. Reinstall the portable tracker "
                "or install Tesseract in C:\\Program Files\\Tesseract-OCR.",
                parent=self.root
            )
            return

        if not CONFIG_PATH.exists():
            self.show_setup_dialog(required=True)
            return

        self.running = True
        self.stop_event.clear()

        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")

        self.status_var.set("Running")

        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True)
        self.worker_thread.start()

    def stop(self):
        if not self.running:
            return

        self.running = False
        self.stop_event.set()

        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")

        self.status_var.set("Stopped")

    def worker_loop(self):
        while not self.stop_event.is_set():
            try:
                result = self.process_capture_once()

                if result:
                    self.ui_queue.put(("match", result))
                else:
                    self.ui_queue.put(("heartbeat",))

            except Exception as e:
                self.ui_queue.put(("error", str(e)))
                break

            time.sleep(CHECK_EVERY_SECONDS)

        self.ui_queue.put(("stopped",))

    def process_capture_once(self):
        image = capture_chatbox()
        image.save(DEBUG_IMAGE_PATH)

        raw_text = read_text_from_image(image)
        messages = combine_wrapped_chat_lines(raw_text)

        for message in messages:
            normalized = normalize_message(message)

            if is_birds_nest_message(normalized):
                message_timestamp = get_message_timestamp(normalized)
                if message_timestamp in self.recent_misc_timestamps:
                    continue

                if message_timestamp:
                    self.recent_misc_timestamps.append(message_timestamp)

                count = increment_misc_counter("birds_nests")
                console_log(f"MISC: Bird's Nests Found: {count}")
                return {
                    "source": "misc",
                    "counter": "birds_nests",
                    "count": count,
                }

            if is_catalyst_message(normalized):
                message_timestamp = get_message_timestamp(normalized)
                if message_timestamp in self.recent_catalyst_timestamps:
                    continue

                parsed_catalyst = parse_catalyst_message(normalized)
                if not parsed_catalyst:
                    console_log("CATALYST MATCH BUT COULD NOT PARSE:", normalized)
                    if message_timestamp:
                        self.recent_catalyst_timestamps.append(message_timestamp)
                    self.ui_queue.put(("catalyst_parse_error", normalized))
                    continue

                quantity, catalyst_item = parsed_catalyst
                if message_timestamp:
                    self.recent_catalyst_timestamps.append(message_timestamp)

                unix_timestamp = int(time.time())
                log_catalyst_drop(unix_timestamp, quantity, catalyst_item)
                console_log(
                    f"CATALYST: {quantity} x {catalyst_item}"
                )
                return {
                    "source": "catalyst",
                    "quantity": quantity,
                    "item": catalyst_item,
                }

            if not is_target_message(normalized):
                continue

            message_timestamp = get_message_timestamp(normalized)
            review_key = message_timestamp or normalized

            if (
                message_timestamp in self.recent_valid_timestamps
                or review_key in self.pending_review_keys
            ):
                continue

            quantity, ocr_reward = extract_reward_components(normalized)
            ranked_matches = rank_reward_matches(ocr_reward) if ocr_reward else []
            reward = choose_automatic_reward(ocr_reward, ranked_matches) if ocr_reward else None

            if quantity is None or reward is None:
                console_log("MATCH BUT COULD NOT PARSE:", normalized)
                if review_key not in self.pending_review_keys:
                    self.pending_review_keys.add(review_key)
                    self.ui_queue.put((
                        "manual_review",
                        {
                            "key": review_key,
                            "message_timestamp": message_timestamp,
                            "raw_message": normalized,
                            "quantity": quantity,
                            "ocr_reward": ocr_reward,
                            "matches": ranked_matches,
                        }
                    ))
                continue

            if message_timestamp:
                self.recent_valid_timestamps.append(message_timestamp)

            unix_timestamp = int(time.time())
            display_timestamp = datetime.fromtimestamp(unix_timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            log_reward(unix_timestamp, quantity, reward)

            if normalize_catalog_text(ocr_reward) != normalize_catalog_text(reward):
                console_log(f"OCR CORRECTED: {ocr_reward} -> {reward}")
            console_log(f"MATCH: {display_timestamp} | {quantity} x {reward}")

            return {
                "source": "seren",
                "quantity": quantity,
                "reward": reward,
            }

        return None

    # ===== Manual OCR review =====

    def show_manual_review(self, review):
        review_key = review["key"]
        existing = self.review_windows.get(review_key)
        if existing and existing.winfo_exists():
            existing.lift()
            return

        window = tk.Toplevel(self.root)
        self.review_windows[review_key] = window
        window.title("Review Uncertain Seren Drop")
        window.geometry("720x520")
        window.minsize(620, 460)
        window.configure(background=COLOR_BG)
        window.transient(self.root)

        outer = tk.Frame(
            window,
            background=COLOR_PANEL,
            highlightbackground=COLOR_GOLD,
            highlightthickness=1,
            padx=14,
            pady=14
        )
        outer.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(
            outer,
            text="Uncertain OCR match",
            background=COLOR_PANEL,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w")

        tk.Label(
            outer,
            text="Review the captured message, correct the quantity if needed, and choose a reward.",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 10)
        ).pack(anchor="w", pady=(2, 10))

        tk.Label(
            outer,
            text="OCR output:",
            background=COLOR_PANEL,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w")

        raw_text = tk.Text(
            outer,
            height=4,
            wrap="word",
            background="#0f0e0b",
            foreground=COLOR_TEXT,
            insertbackground=COLOR_GOLD,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 10)
        )
        raw_text.pack(fill="x", pady=(3, 10))
        raw_text.insert("1.0", review["raw_message"])
        raw_text.configure(state="disabled")

        details = tk.Frame(outer, background=COLOR_PANEL)
        details.pack(fill="x", pady=(0, 10))

        tk.Label(
            details,
            text="Quantity:",
            background=COLOR_PANEL,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 10, "bold")
        ).pack(side="left")

        quantity_var = tk.StringVar(
            value="" if review["quantity"] is None else str(review["quantity"])
        )
        quantity_entry = tk.Entry(
            details,
            textvariable=quantity_var,
            width=10,
            background="#0f0e0b",
            foreground=COLOR_TEXT,
            insertbackground=COLOR_GOLD,
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 10)
        )
        quantity_entry.pack(side="left", padx=(8, 22))

        tk.Label(
            details,
            text=f"Reward read as: {review['ocr_reward'] or '(unavailable)'}",
            background=COLOR_PANEL,
            foreground=COLOR_TEXT,
            font=("Segoe UI", 10)
        ).pack(side="left")

        tk.Label(
            outer,
            text="Closest catalogue matches:",
            background=COLOR_PANEL,
            foreground=COLOR_GOLD,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", pady=(0, 3))

        selected_reward = tk.StringVar(
            value=review["matches"][0][0] if review["matches"] else ""
        )

        choices = tk.Frame(outer, background=COLOR_PANEL)
        choices.pack(fill="x")

        for reward, score in review["matches"]:
            tk.Radiobutton(
                choices,
                text=f"{reward}  ({score * 100:.1f}%)",
                variable=selected_reward,
                value=reward,
                background=COLOR_PANEL,
                activebackground=COLOR_ROW_ALT,
                foreground=COLOR_TEXT,
                activeforeground=COLOR_GOLD,
                selectcolor="#0f0e0b",
                anchor="w",
                font=("Segoe UI", 11),
                padx=5,
                pady=4
            ).pack(fill="x", anchor="w")

        buttons = tk.Frame(outer, background=COLOR_PANEL)
        buttons.pack(side="bottom", fill="x", pady=(14, 0))

        ttk.Button(
            buttons,
            text="Log Selected Reward",
            style="Start.TButton",
            command=lambda: self.log_manual_review(
                review, quantity_var.get(), selected_reward.get()
            )
        ).pack(side="left")

        ttk.Button(
            buttons,
            text="Ignore This Drop",
            style="Stop.TButton",
            command=lambda: self.ignore_manual_review(review)
        ).pack(side="left", padx=8)

        window.protocol("WM_DELETE_WINDOW", lambda: self.ignore_manual_review(review))
        window.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - window.winfo_width()) // 2)
        y = self.root.winfo_y() + max(0, (self.root.winfo_height() - window.winfo_height()) // 2)
        window.geometry(f"+{x}+{y}")
        window.lift()
        quantity_entry.focus_set()
        self.flash_taskbar_icon()

    def close_review_window(self, review_key):
        window = self.review_windows.pop(review_key, None)
        if window and window.winfo_exists():
            window.destroy()

    def log_manual_review(self, review, quantity_text, reward):
        try:
            quantity = int(quantity_text)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "Invalid quantity",
                "Enter a positive whole-number quantity.",
                parent=self.review_windows.get(review["key"])
            )
            return

        if reward not in VALID_REWARDS:
            messagebox.showerror(
                "No reward selected",
                "Select one of the catalogue matches before logging.",
                parent=self.review_windows.get(review["key"])
            )
            return

        unix_timestamp = int(time.time())
        display_timestamp = datetime.fromtimestamp(unix_timestamp).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        log_reward(unix_timestamp, quantity, reward)

        if review["message_timestamp"]:
            self.recent_valid_timestamps.append(review["message_timestamp"])
        self.pending_review_keys.discard(review["key"])
        self.close_review_window(review["key"])

        console_log(f"MANUALLY RESOLVED: {display_timestamp} | {quantity} x {reward}")
        self.reload_tables()
        self.status_var.set(f"Manually logged: {quantity} x {reward}")
        if reward not in self.ge_prices:
            self.refresh_prices()

    def ignore_manual_review(self, review):
        if review["message_timestamp"]:
            self.recent_valid_timestamps.append(review["message_timestamp"])
        self.pending_review_keys.discard(review["key"])
        self.close_review_window(review["key"])
        self.status_var.set("Uncertain drop ignored")

    def process_ui_queue(self):
        try:
            while True:
                item = self.ui_queue.get_nowait()
                kind = item[0]

                if kind == "match":
                    result = item[1]
                    self.reload_tables()
                    if result.get("source") == "misc":
                        self.status_var.set(
                            f"Bird's Nests Found: {result['count']}"
                        )
                    elif result.get("source") == "catalyst":
                        self.status_var.set(
                            f"Logged catalyst: {result['quantity']} x "
                            f"{result['item']}"
                        )
                    else:
                        self.status_var.set(
                            f"Logged: {result['quantity']} x {result['reward']}"
                        )
                        if result["reward"] not in self.ge_prices:
                            self.refresh_prices()

                elif kind == "prices":
                    prices, requested_count = item[1], item[2]
                    self.price_refreshing = False
                    self.ge_prices = prices
                    self.reload_tables()
                    self.status_var.set(
                        f"Loaded GE prices for {len(prices)} of {requested_count} items"
                    )

                elif kind == "price_error":
                    self.price_refreshing = False
                    self.status_var.set(f"GE price refresh failed: {item[1]}")

                elif kind == "manual_review":
                    self.status_var.set("Uncertain match requires review")
                    self.show_manual_review(item[1])

                elif kind == "catalyst_parse_error":
                    self.status_var.set("Catalyst message could not be parsed")
                    self.flash_taskbar_icon()
                    messagebox.showwarning(
                        "Catalyst drop needs review",
                        "A catalyst message was detected, but its quantity or item "
                        f"could not be read:\n\n{item[1]}",
                        parent=self.root
                    )

                elif kind == "heartbeat":
                    if self.running:
                        self.status_var.set("Running - watching for drops...")

                elif kind == "error":
                    self.stop()
                    messagebox.showerror("Watcher error", item[1])

                elif kind == "stopped":
                    self.running = False
                    self.start_button.config(state="normal")
                    self.stop_button.config(state="disabled")
                    self.status_var.set("Stopped")

        except queue.Empty:
            pass

        self.root.after(100, self.process_ui_queue)

    def quit(self):
        self.stop()
        self.root.after(150, self.root.destroy)

    def reload_app(self):
        self.stop_event.set()
        self.running = False

        # Let an in-progress OCR/storage cycle finish before the replacement
        # process imports or synchronizes persistent data.
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=3)

        executable = Path(sys.executable)
        if getattr(sys, "frozen", False):
            command = [str(executable)]
            working_directory = DATA_DIR
        else:
            script_path = Path(__file__).resolve()
            if sys.platform == "win32":
                windowed_executable = executable.with_name("pythonw.exe")
                if windowed_executable.exists():
                    executable = windowed_executable
            command = [str(executable), str(script_path)]
            working_directory = script_path.parent

        subprocess.Popen(
            command,
            cwd=str(working_directory)
        )
        self.root.destroy()


# ===== RUN =====

if __name__ == "__main__":
    try:
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_USER_MODEL_ID
            )
        except Exception:
            pass

        root = tk.Tk()
        app = SerenWatcherGUI(root)
        root.protocol("WM_DELETE_WINDOW", app.quit)
        root.mainloop()

    except Exception:
        import traceback

        details = traceback.format_exc()
        ERROR_LOG_PATH.write_text(details, encoding="utf-8")

        if sys.stderr is not None:
            sys.stderr.write(details)

        try:
            error_root = tk.Tk()
            error_root.withdraw()
            messagebox.showerror(
                "Seren Spirit Watcher Error",
                f"The watcher could not start. Details were saved to:\n{ERROR_LOG_PATH}"
            )
            error_root.destroy()
        except Exception:
            pass
