"""
eldensave.savefile
===================
Core logic for reading and writing Elden Ring (.sl2) PC save files.

File layout (PC, ER0000.sl2):
    [0x000000 - 0x0002FF]  header                       (0x300 bytes)
    for each of the 10 character slots (i = 0..9):
        [slot_start + 0x00 - 0x0F]   16-byte MD5 checksum of the slot's data
        [slot_start + 0x10 - ...  ]  16 bytes padding/IV area (unused, kept as-is)
        [slot_start + 0x20 - ... ]   raw slot data (character save, plaintext)
        slot stride = 0x280010 bytes
    [after last slot]                     regulation / profile-summary block,
                                           including a "general" MD5 checksum
                                           at 0x019003A0 covering 0x019003B0-0x019603AF

Important: unlike Dark Souls Remastered, Elden Ring does NOT AES-encrypt the
per-slot contents on PC -- integrity is enforced purely with MD5 checksums.
This matches the behaviour of established community tools (e.g. Ariescyn's
Elden Ring Save Manager, ClayAmore's ER-Save-Editor/ER-Save-Lib). All offsets
below are empirically documented facts about the file format, not anyone's
copyrighted code.

Within a single (checksum-stripped) character slot, there is no single fixed
offset for stats -- instead we locate an anchor point by walking the
"ga item handle" list (an array of equipped/possessed item handles starting
at offset 0x20), and compute every other field as a fixed distance from the
end of that list. This mirrors how the game itself resolves these fields.
"""

from __future__ import annotations

import hashlib
import struct
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

HEADER_SIZE = 0x300
SLOT_STRIDE = 0x280010          # distance between the start of one slot and the next
SLOT_CHECKSUM_LEN = 16          # MD5 digest
SLOT_DATA_LEN = 2621439         # 0x27FFFF -> bytes covered by the per-slot MD5
SLOT_PREFIX = 16                # bytes stripped from the front of each raw slot
NUM_SLOTS = 10

GENERAL_CHECKSUM_OFFSET = 0x019003A0
GENERAL_CHECKSUM_LEN = 16
GENERAL_DATA_START = 0x019003B0
GENERAL_DATA_END = 0x019603AF  # inclusive

# distance from end of the ga-item-handle list to the "magic" stat anchor
GA_TO_MAGIC = 0x1AF
# distance from the magic anchor back to the character name (32 bytes, UTF-16LE)
MAGIC_TO_NAME = -0x11B
NAME_LEN_BYTES = 32
# distance from the magic anchor to the runes (4 bytes) / total soul memory (4 bytes)
MAGIC_TO_RUNES = -331
MAGIC_TO_NG_PATTERN_DISTANCE = -280
NG_PLUS_PATTERN = bytes.fromhex("FF FF FF FF 00 00 00 00 00 00 00 00 00 01")

# stat name -> distance from magic anchor, (byte size)
STAT_OFFSETS = {
    "level": (-335, 2),
    "vigor": (-379, 1),
    "mind": (-375, 1),
    "endurance": (-371, 1),
    "strength": (-367, 1),
    "dexterity": (-363, 1),
    "intelligence": (-359, 1),
    "faith": (-355, 1),
    "arcane": (-351, 1),
    "gender": (-249, 1),
    "class": (-248, 1),
    "scadutree_blessing": (-187, 1),
    "shadow_realm_blessing": (-186, 1),
}

GENDER_MAP = {1: "Male", 0: "Female"}
REVERSE_GENDER_MAP = {v: k for k, v in GENDER_MAP.items()}

CLASS_MAP = {
    0: "Vagabond", 1: "Warrior", 2: "Hero", 3: "Bandit", 4: "Astrologer",
    5: "Prophet", 6: "Confessor", 7: "Samurai", 8: "Prisoner", 9: "Wretch",
}
REVERSE_CLASS_MAP = {v: k for k, v in CLASS_MAP.items()}

# item-handle high nibble -> category, used while walking the ga-item list
ITEM_TYPE_EMPTY = 0x00000000
ITEM_TYPE_WEAPON = 0x80000000
ITEM_TYPE_ARMOR = 0x90000000
ITEM_TYPE_RINGS = 0xA0000000
ITEM_TYPE_GOOD = 0xB0000000
ITEM_TYPE_AOW = 0xC0000000

ITEM_TYPE_NAMES = {
    ITEM_TYPE_EMPTY: "empty",
    ITEM_TYPE_WEAPON: "weapon",
    ITEM_TYPE_ARMOR: "armor",
    ITEM_TYPE_RINGS: "talisman",
    ITEM_TYPE_GOOD: "good",
    ITEM_TYPE_AOW: "ash_of_war",
}

GA_ITEM_LIST_START = 0x20
GA_ITEM_LIST_SLOTS = 5120
GA_ITEM_BASE_SIZE = 8

INVENTORY_ITEM_SIZE = 12
INVENTORY_START_FROM_GA_END = 505 + GA_TO_MAGIC
INVENTORY_END_FROM_GA_END = 37365 + GA_TO_MAGIC


class SaveFormatError(Exception):
    """Raised when the .sl2 structure doesn't look like a valid Elden Ring save."""


@dataclass
class GaItem:
    handle: int
    item_id: int
    offset: int
    size: int


@dataclass
class InventoryItem:
    handle: int
    quantity: int
    index: int
    offset: int

    @property
    def category(self) -> str:
        return ITEM_TYPE_NAMES.get(self.handle & 0xF0000000, "unknown")


@dataclass
class CharacterSlot:
    """A single (decrypted/stripped) character slot's mutable byte buffer."""

    index: int
    data: bytearray
    _ga_end: Optional[int] = field(default=None, repr=False)

    # -- anchor resolution --------------------------------------------------

    def _walk_ga_items(self) -> int:
        """Walk the ga-item-handle list starting at 0x20 and return the
        offset immediately after it ends. Result is cached."""
        if self._ga_end is not None:
            return self._ga_end

        offset = GA_ITEM_LIST_START
        for _ in range(GA_ITEM_LIST_SLOTS):
            handle, _item_id = struct.unpack_from("<II", self.data, offset)
            size = GA_ITEM_BASE_SIZE
            if handle != 0:
                type_bits = handle & 0xF0000000
                if type_bits == ITEM_TYPE_WEAPON:
                    size = GA_ITEM_BASE_SIZE + 12 + 1
                elif type_bits == ITEM_TYPE_ARMOR:
                    size = GA_ITEM_BASE_SIZE + 8
            offset += size

        self._ga_end = offset
        return offset

    def _magic_offset(self) -> int:
        return self._walk_ga_items() + GA_TO_MAGIC

    def is_empty(self) -> bool:
        """An empty slot has no valid character name."""
        return self.character_name() is None

    # -- reading --------------------------------------------------------

    def character_name(self) -> Optional[str]:
        magic = self._magic_offset()
        name_offset = magic + MAGIC_TO_NAME
        raw = bytes(self.data[name_offset:name_offset + NAME_LEN_BYTES])
        name = raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
        return name or None

    def set_character_name(self, new_name: str) -> None:
        magic = self._magic_offset()
        name_offset = magic + MAGIC_TO_NAME
        encoded = new_name.encode("utf-16-le")[:NAME_LEN_BYTES].ljust(NAME_LEN_BYTES, b"\x00")
        self.data[name_offset:name_offset + NAME_LEN_BYTES] = encoded

    def runes(self) -> int:
        magic = self._magic_offset()
        offset = magic + MAGIC_TO_RUNES
        return struct.unpack_from("<I", self.data, offset)[0]

    def set_runes(self, value: int) -> None:
        if not (0 <= value <= 0xFFFFFFFF):
            raise ValueError("runes must fit in an unsigned 32-bit integer (0 - 4294967295)")
        magic = self._magic_offset()
        offset = magic + MAGIC_TO_RUNES
        # keep "total souls memory" (used for future level-up cost scaling)
        # in sync the same way the game does: it only ever grows, so we bump
        # it by the delta being applied.
        current = struct.unpack_from("<I", self.data, offset)[0]
        memory = struct.unpack_from("<I", self.data, offset + 4)[0]
        delta = (value - current) % 0x100000000
        new_memory = (memory + delta) % 0x100000000
        struct.pack_into("<I", self.data, offset, value)
        struct.pack_into("<I", self.data, offset + 4, new_memory)

    def stat(self, name: str) -> int:
        name = name.lower()
        if name not in STAT_OFFSETS:
            raise KeyError(f"unknown stat '{name}', expected one of {list(STAT_OFFSETS)}")
        distance, size = STAT_OFFSETS[name]
        offset = self._magic_offset() + distance
        raw = int.from_bytes(self.data[offset:offset + size], "little")
        if name == "gender":
            return raw  # caller can map via GENDER_MAP
        if name == "class":
            return raw  # caller can map via CLASS_MAP
        return raw

    def set_stat(self, name: str, value: int) -> None:
        name = name.lower()
        if name not in STAT_OFFSETS:
            raise KeyError(f"unknown stat '{name}', expected one of {list(STAT_OFFSETS)}")
        distance, size = STAT_OFFSETS[name]
        max_val = (1 << (size * 8)) - 1
        if not (0 <= value <= max_val):
            raise ValueError(f"{name} must be between 0 and {max_val}")
        offset = self._magic_offset() + distance
        self.data[offset:offset + size] = value.to_bytes(size, "little")

    def all_stats(self) -> dict:
        result = {}
        for name in STAT_OFFSETS:
            raw = self.stat(name)
            if name == "gender":
                result[name] = GENDER_MAP.get(raw, f"unknown({raw})")
            elif name == "class":
                result[name] = CLASS_MAP.get(raw, f"unknown({raw})")
            else:
                result[name] = raw
        return result

    def new_game_plus(self) -> Optional[int]:
        pattern_offset = self.data.find(NG_PLUS_PATTERN)
        if pattern_offset == -1:
            return None
        offset = pattern_offset + MAGIC_TO_NG_PATTERN_DISTANCE
        return self.data[offset]

    def set_new_game_plus(self, value: int) -> None:
        if not (0 <= value <= 255):
            raise ValueError("NG+ value must be between 0 and 255")
        pattern_offset = self.data.find(NG_PLUS_PATTERN)
        if pattern_offset == -1:
            raise SaveFormatError("could not locate NG+ anchor pattern in this slot")
        offset = pattern_offset + MAGIC_TO_NG_PATTERN_DISTANCE
        self.data[offset] = value

    # -- inventory (read-only for now) -----------------------------------

    def ga_items(self) -> list[GaItem]:
        items = []
        offset = GA_ITEM_LIST_START
        for _ in range(GA_ITEM_LIST_SLOTS):
            handle, item_id = struct.unpack_from("<II", self.data, offset)
            size = GA_ITEM_BASE_SIZE
            if handle != 0:
                type_bits = handle & 0xF0000000
                if type_bits == ITEM_TYPE_WEAPON:
                    size = GA_ITEM_BASE_SIZE + 12 + 1
                elif type_bits == ITEM_TYPE_ARMOR:
                    size = GA_ITEM_BASE_SIZE + 8
                if handle != 0:
                    items.append(GaItem(handle, item_id, offset, size))
            offset += size
        return items

    def inventory(self) -> list[InventoryItem]:
        ga_end = self._walk_ga_items()
        start = ga_end + INVENTORY_START_FROM_GA_END
        end = ga_end + INVENTORY_END_FROM_GA_END
        items = []
        offset = start
        while offset < end:
            handle, qty, index = struct.unpack_from("<III", self.data, offset)
            if handle != 0:
                items.append(InventoryItem(handle, qty, index, offset))
            offset += INVENTORY_ITEM_SIZE
        return items

    def _inventory_all_slots(self) -> list[InventoryItem]:
        """Like inventory(), but includes empty (handle==0) slots too --
        needed to find free space to write a new item into."""
        ga_end = self._walk_ga_items()
        start = ga_end + INVENTORY_START_FROM_GA_END
        end = ga_end + INVENTORY_END_FROM_GA_END
        items = []
        offset = start
        while offset < end:
            handle, qty, index = struct.unpack_from("<III", self.data, offset)
            items.append(InventoryItem(handle, qty, index, offset))
            offset += INVENTORY_ITEM_SIZE
        return items

    def _ga_classify(self) -> dict:
        """Walk the ga-item-handle list and bucket every entry (including
        empty placeholder slots) by type. Returns offsets so callers can
        insert/delete entries in place."""
        buckets = {"weapons": [], "armors": [], "aow": [], "empty": [], "all": []}
        offset = GA_ITEM_LIST_START
        for _ in range(GA_ITEM_LIST_SLOTS):
            handle, item_id = struct.unpack_from("<II", self.data, offset)
            size = GA_ITEM_BASE_SIZE
            type_bits = handle & 0xF0000000
            if handle != 0:
                if type_bits == ITEM_TYPE_WEAPON:
                    size = GA_ITEM_BASE_SIZE + 12 + 1
                elif type_bits == ITEM_TYPE_ARMOR:
                    size = GA_ITEM_BASE_SIZE + 8

            entry = (handle, item_id, offset)
            buckets["all"].append(entry)
            if handle == 0:
                buckets["empty"].append(entry)
            elif type_bits == ITEM_TYPE_WEAPON:
                buckets["weapons"].append(entry)
            elif type_bits == ITEM_TYPE_ARMOR:
                buckets["armors"].append(entry)
            elif type_bits == ITEM_TYPE_AOW:
                buckets["aow"].append(entry)

            offset += size
        return buckets

    def _inventory_counters_bump(self) -> None:
        magic = self._magic_offset()
        for distance in (501, 37373, 37377):
            offset = magic + distance
            val = struct.unpack_from("<H", self.data, offset)[0]
            struct.pack_into("<H", self.data, offset, (val + 1) & 0xFFFF)

    def add_good(self, item_id: bytes, quantity: int = 1) -> None:
        """Add (or top up) a stackable "goods"-type item (consumables,
        Bell Bearings, key items, etc.) directly into the character's
        inventory. Does not touch the ga-item-handle table -- goods are
        referenced directly by item ID, so this is a low-risk operation
        that never resizes the file."""
        if len(item_id) != 4:
            raise ValueError("item_id must be exactly 4 bytes")
        item_id_int = int.from_bytes(item_id, "little")

        existing = self.inventory()
        for item in existing:
            if item.handle == item_id_int:
                struct.pack_into("<I", self.data, item.offset + 4, quantity)
                return

        all_slots = self._inventory_all_slots()
        empty_slots = [s for s in all_slots if s.handle == 0]
        if not empty_slots:
            raise SaveFormatError("no empty inventory slot available to add this item")

        non_empty = [s for s in all_slots if s.handle != 0]
        highest_index = non_empty[-1].index if non_empty else 0
        new_index = highest_index + 2

        target = empty_slots[0]
        struct.pack_into("<III", self.data, target.offset, item_id_int, quantity, new_index)
        self._inventory_counters_bump()

    def _add_ga_item(self, item_id: bytes, category: str) -> None:
        """Shared logic for weapons, armor, and ashes of war: each needs its
        own entry in the ga-item-handle table (so the game can track
        upgrade level / affinity / which weapon it's affixed to for that
        specific instance). This grows that table and shrinks an existing
        empty placeholder + the very end of the slot buffer to compensate,
        so total file size never changes (for Ashes of War the new entry is
        exactly as big as the placeholder it replaces, so no trimming is
        even needed). This is the highest-risk family of operations in this
        tool; a `.bak` backup is always made before any save() call, and a
        length/consistency check runs before committing."""
        if category == "weapon":
            high_word = 0x8080
            extra = bytes(13)  # entry = 4 (handle) + 4 (item id) + 13 = 21 bytes
            anchor_direction = "after"
        elif category == "armor":
            high_word = 0x9080
            extra = bytes(8)   # entry = 4 (handle) + 4 (item id) + 8 = 16 bytes
            anchor_direction = "after"
        elif category == "aow":
            high_word = 0xC080
            extra = bytes(0)   # entry = 4 (handle) + 4 (item id) + 0 = 8 bytes (same as empty)
            anchor_direction = "before"
        else:
            raise ValueError(f"unsupported category {category!r}")

        if len(item_id) != 4:
            raise ValueError("item_id must be exactly 4 bytes")

        original = bytearray(self.data)
        original_len = len(self.data)

        buckets = self._ga_classify()
        if not buckets["weapons"]:
            raise SaveFormatError(
                "no existing weapon found in the ga-item table to anchor the insert point; "
                "refusing to guess a safe location"
            )
        if not buckets["empty"]:
            raise SaveFormatError("no empty ga-item slot available to compensate for the new entry")

        # weapons/armor are anchored just after the first weapon block;
        # ashes of war are grouped just before it.
        first_weapon_offset = buckets["weapons"][0][2]
        insert_offset = None
        for handle, _item_id, offset in buckets["empty"]:
            if anchor_direction == "after" and offset > first_weapon_offset:
                insert_offset = offset
                break
            if anchor_direction == "before" and offset < first_weapon_offset:
                insert_offset = offset
                break
        if insert_offset is None:
            raise SaveFormatError(f"could not find an empty ga-item slot {anchor_direction} the weapon block")

        highest_ga = max((handle & 0x0000FFFF for handle, _iid, _off in buckets["all"] if handle != 0), default=0)
        new_ga_index = highest_ga + 1
        new_handle = new_ga_index.to_bytes(2, "little") + high_word.to_bytes(2, "little")
        new_entry = new_handle + item_id + extra

        # 1) insert the new entry
        self.data[insert_offset:insert_offset] = new_entry

        # 2) delete one empty placeholder (8 bytes) to compensate -- pick the
        #    highest-offset empty slot, adjusting for the shift we just made
        last_empty_offset = max(offset for _h, _iid, offset in buckets["empty"])
        delete_offset = last_empty_offset
        if insert_offset <= last_empty_offset:
            delete_offset += len(new_entry)

        placeholder = bytes(self.data[delete_offset:delete_offset + GA_ITEM_BASE_SIZE])
        if placeholder != b"\x00\x00\x00\x00\xff\xff\xff\xff":
            self.data = original
            raise SaveFormatError(
                "empty ga-item placeholder did not look as expected; rolled back, no changes made"
            )
        del self.data[delete_offset:delete_offset + GA_ITEM_BASE_SIZE]

        # 3) net growth so far is +len(extra)+4 -8 bytes; trim that many
        #    bytes off the very end of the slot buffer to keep the total
        #    file size fixed (zero for Ashes of War, since their entry is
        #    exactly the size of the placeholder it replaced)
        trim = len(new_entry) - GA_ITEM_BASE_SIZE
        self.data = self.data[:-trim] if trim > 0 else self.data

        if len(self.data) != original_len:
            self.data = original
            raise SaveFormatError(
                f"slot length changed unexpectedly ({len(self.data)} != {original_len}); rolled back"
            )

        # cache is now stale since the ga-item table moved
        self._ga_end = None

        # 4) add the corresponding inventory entry (quantity is always 1)
        try:
            self.add_good(new_handle, quantity=1)
        except Exception:
            self.data = original
            self._ga_end = None
            raise

    def add_weapon(self, item_id: bytes) -> None:
        """Add a weapon to the character's inventory. See _add_ga_item for
        how this works under the hood."""
        self._add_ga_item(item_id, category="weapon")

    def add_armor(self, item_id: bytes) -> None:
        """Add an armor piece to the character's inventory. See
        _add_ga_item for how this works under the hood."""
        self._add_ga_item(item_id, category="armor")

    def add_aow(self, item_id: bytes) -> None:
        """Add an Ash of War to the character's inventory. See
        _add_ga_item for how this works under the hood."""
        self._add_ga_item(item_id, category="aow")


class EldenRingSave:
    """Represents a full ER0000.sl2 file: header + 10 character slots + regulation."""

    def __init__(self, header: bytes, slots: list[CharacterSlot], regulation: bytes):
        self.header = bytearray(header)
        self.slots = slots
        self.regulation = bytearray(regulation)

    # -- loading / saving -------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "EldenRingSave":
        path = Path(path)
        raw = path.read_bytes()
        if len(raw) < HEADER_SIZE + NUM_SLOTS * SLOT_STRIDE:
            raise SaveFormatError(
                f"file too small ({len(raw)} bytes) to be a valid ER0000.sl2"
            )

        header = raw[:HEADER_SIZE]

        slots = []
        cursor = HEADER_SIZE
        for i in range(NUM_SLOTS):
            chunk = raw[cursor:cursor + SLOT_STRIDE]
            slot_data = bytearray(chunk[SLOT_PREFIX:])
            slots.append(CharacterSlot(index=i, data=slot_data))
            cursor += SLOT_STRIDE

        regulation = raw[cursor:]
        return cls(header, slots, regulation)

    def save(self, path: str | Path, backup: bool = True) -> None:
        """Recomputes all checksums and writes the file back out.

        If `backup` is True and `path` already exists, a `<name>.bak` copy
        of the *current on-disk* file is made first (never overwritten).
        """
        path = Path(path)
        if backup and path.exists():
            backup_path = path.with_suffix(path.suffix + ".bak")
            if not backup_path.exists():
                shutil.copy2(path, backup_path)

        raw = bytearray()
        raw += self.header

        slot_payload_checksums = []
        for slot in self.slots:
            padded = bytes(SLOT_PREFIX) + bytes(slot.data)
            # pad/truncate to the fixed stride
            padded = (padded + bytes(SLOT_STRIDE))[:SLOT_STRIDE]
            slot_payload_checksums.append(padded)

        raw += b"".join(slot_payload_checksums)
        raw += self.regulation

        raw = self._recalc_checksums(raw)
        path.write_bytes(bytes(raw))

    @staticmethod
    def _recalc_checksums(raw: bytearray) -> bytearray:
        s_ind = 0x310  # start of slot #0's *data* (after its 16-byte checksum + 16-byte pad)
        c_ind = 0x300  # start of slot #0's checksum
        for _ in range(NUM_SLOTS):
            slot_data = bytes(raw[s_ind:s_ind + SLOT_DATA_LEN + 1])
            new_cs = hashlib.md5(slot_data).digest()
            raw[c_ind:c_ind + SLOT_CHECKSUM_LEN] = new_cs
            s_ind += SLOT_STRIDE
            c_ind += SLOT_STRIDE

        if len(raw) > GENERAL_DATA_END:
            general = bytes(raw[GENERAL_DATA_START:GENERAL_DATA_END + 1])
            new_general_cs = hashlib.md5(general).digest()
            raw[GENERAL_CHECKSUM_OFFSET:GENERAL_CHECKSUM_OFFSET + GENERAL_CHECKSUM_LEN] = new_general_cs

        return raw

    # -- convenience --------------------------------------------------------

    def active_slots(self) -> list[CharacterSlot]:
        return [s for s in self.slots if not s.is_empty()]

    def summary(self) -> list[dict]:
        out = []
        for slot in self.slots:
            name = slot.character_name()
            if name is None:
                out.append({"slot": slot.index, "empty": True})
                continue
            out.append({
                "slot": slot.index,
                "empty": False,
                "name": name,
                "level": slot.stat("level"),
                "runes": slot.runes(),
                "class": CLASS_MAP.get(slot.stat("class"), "unknown"),
                "ng_plus": slot.new_game_plus(),
            })
        return out
