"""
eldensave.items
================
Item ID database. Item IDs are documented, public facts about Elden Ring's
internal param tables (the same IDs are used by Cheat Engine tables,
UXM-based tools, and every other save/memory editor for this game) -- not
anyone's copyrighted code. The full tables (`data/*_full.json`) were
compiled from those same public, community-maintained ID lists.

Each ID is stored as a 4-byte little-endian hex string, matching the
in-file representation.
"""

import json
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"


def _load(name: str) -> dict:
    path = _DATA_DIR / name
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# Full databases (name -> 4-byte little-endian hex string)
WEAPONS_FULL = _load("weapons_full.json")
ARMOR_FULL = _load("armor_full.json")
TALISMANS_FULL = _load("talismans_full.json")
GOODS_FULL = _load("goods_full.json")
SPELLS_FULL = _load("spells_full.json")  # sorceries + incantations
AOW_FULL = _load("aow_full.json")  # Ashes of War

# Bell Bearings are just goods whose name contains "Bell Bearing" -- filter
# them out of the full goods table so `add-all-bearings` doesn't have to
# hand-maintain a separate list.
BELL_BEARINGS = {
    name: hexid for name, hexid in GOODS_FULL.items() if "bell bearing" in name.lower()
}

# Aliases used elsewhere in the CLI/README.
WEAPONS = WEAPONS_FULL
ARMOR = ARMOR_FULL
TALISMANS = TALISMANS_FULL
GOODS = GOODS_FULL

RAGING_WOLF_SET = [
    "Raging Wolf Helm",
    "Raging Wolf Armor",
    "Raging Wolf Gauntlets",
    "Raging Wolf Greaves",
]


def item_id_bytes(hex_str: str) -> bytes:
    """Convert a 4-byte little-endian hex string (as stored above) to raw bytes."""
    b = bytes.fromhex(hex_str)
    if len(b) != 4:
        raise ValueError(f"expected 4 bytes, got {len(b)} for {hex_str!r}")
    return b


def find(name: str, table: dict) -> str:
    """Case-insensitive exact-name lookup in one of the *_FULL dicts.
    Raises KeyError with close suggestions if not found."""
    if name in table:
        return table[name]
    lowered = name.lower()
    for key, val in table.items():
        if key.lower() == lowered:
            return val
    suggestions = [k for k in table if lowered in k.lower()][:10]
    raise KeyError(f"{name!r} not found. Did you mean: {suggestions}" if suggestions else f"{name!r} not found.")
