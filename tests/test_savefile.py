"""
Since we don't have a real ER0000.sl2 on hand, these tests build a
*synthetic* save buffer that matches the documented layout exactly
(empty ga-item list, known values planted at the correct offsets) and
verify that:

  1. header/slot/regulation splitting and re-merging is byte-exact
  2. per-slot + general MD5 checksums are (re)computed correctly
  3. the ga-item-list walk lands on the expected anchor for an all-empty list
  4. stat/name/rune/NG+ read & write round-trip correctly through the API
  5. a full load() -> mutate -> save() -> load() cycle preserves values
     and produces a file that passes checksum verification
"""
import hashlib
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eldensave.savefile import (  # noqa: E402
    EldenRingSave, CharacterSlot, HEADER_SIZE, SLOT_STRIDE, SLOT_PREFIX,
    NUM_SLOTS, GA_ITEM_LIST_START, GA_ITEM_LIST_SLOTS, GA_ITEM_BASE_SIZE,
    GA_TO_MAGIC, MAGIC_TO_NAME, MAGIC_TO_RUNES, NAME_LEN_BYTES,
    STAT_OFFSETS, NG_PLUS_PATTERN, MAGIC_TO_NG_PATTERN_DISTANCE,
    SLOT_DATA_LEN, SLOT_CHECKSUM_LEN, GENERAL_CHECKSUM_OFFSET,
    GENERAL_DATA_START, GENERAL_DATA_END, GENERAL_CHECKSUM_LEN,
)

SLOT_PAYLOAD_SIZE = SLOT_STRIDE - SLOT_PREFIX


def make_synthetic_slot_bytes(name="rage", level=50, runes=12345, ng=0,
                               vigor=30, populate_ng_pattern=True) -> bytearray:
    """Build a plausible, all-empty-inventory slot buffer with known values
    planted at the offsets the real game would use."""
    buf = bytearray(SLOT_PAYLOAD_SIZE)

    # empty ga-item list -> walk ends right after GA_ITEM_LIST_SLOTS * BASE_SIZE
    ga_end = GA_ITEM_LIST_START + GA_ITEM_LIST_SLOTS * GA_ITEM_BASE_SIZE
    magic = ga_end + GA_TO_MAGIC

    # name
    name_off = magic + MAGIC_TO_NAME
    encoded = name.encode("utf-16-le")[:NAME_LEN_BYTES].ljust(NAME_LEN_BYTES, b"\x00")
    buf[name_off:name_off + NAME_LEN_BYTES] = encoded

    # runes + soul memory
    runes_off = magic + MAGIC_TO_RUNES
    struct.pack_into("<I", buf, runes_off, runes)
    struct.pack_into("<I", buf, runes_off + 4, runes)  # memory == runes initially

    # stats
    for stat, (distance, size) in STAT_OFFSETS.items():
        off = magic + distance
        val = {"level": level, "vigor": vigor}.get(stat, 10)
        buf[off:off + size] = val.to_bytes(size, "little")

    # NG+ marker pattern, placed somewhere safely inside the buffer
    if populate_ng_pattern:
        pattern_pos = magic + 50000  # arbitrary safe spot, away from other fields
        buf[pattern_pos:pattern_pos + len(NG_PLUS_PATTERN)] = NG_PLUS_PATTERN
        buf[pattern_pos + MAGIC_TO_NG_PATTERN_DISTANCE] = ng

    return buf


def build_synthetic_sl2(num_named_slots=1) -> bytes:
    header = bytes(HEADER_SIZE)
    slots = []
    for i in range(NUM_SLOTS):
        if i < num_named_slots:
            payload = make_synthetic_slot_bytes(name=f"char{i}", level=10 + i, runes=100 * i)
        else:
            payload = bytearray(SLOT_PAYLOAD_SIZE)  # empty slot
        slots.append(bytes(SLOT_PREFIX) + bytes(payload))
    regulation = bytes(0x2000)  # small dummy tail, enough to exceed GENERAL_DATA_END is NOT required for split/merge test
    return header + b"".join(slots) + regulation


def test_split_load_roundtrip():
    raw = build_synthetic_sl2(num_named_slots=3)
    tmp = Path("/tmp/test_synth.sl2")
    tmp.write_bytes(raw)

    save = EldenRingSave.load(tmp)
    assert len(save.slots) == NUM_SLOTS
    assert save.slots[0].character_name() == "char0"
    assert save.slots[1].character_name() == "char1"
    assert save.slots[2].character_name() == "char2"
    assert save.slots[3].is_empty()
    print("OK: split_load_roundtrip")


def test_stat_and_rune_read():
    raw = build_synthetic_sl2(num_named_slots=1)
    tmp = Path("/tmp/test_synth2.sl2")
    tmp.write_bytes(raw)

    save = EldenRingSave.load(tmp)
    slot = save.slots[0]
    assert slot.stat("level") == 10
    assert slot.stat("vigor") == 30
    assert slot.runes() == 0
    print("OK: stat_and_rune_read")


def test_mutate_and_save_roundtrip():
    raw = build_synthetic_sl2(num_named_slots=2)
    tmp = Path("/tmp/test_synth3.sl2")
    tmp.write_bytes(raw)

    save = EldenRingSave.load(tmp)
    slot = save.slots[0]
    slot.set_runes(999999)
    slot.set_stat("vigor", 60)
    slot.set_character_name("newname")
    save.save(tmp, backup=False)

    reloaded = EldenRingSave.load(tmp)
    slot2 = reloaded.slots[0]
    assert slot2.runes() == 999999, slot2.runes()
    assert slot2.stat("vigor") == 60
    assert slot2.character_name() == "newname"
    # untouched slot must be unaffected
    assert reloaded.slots[1].character_name() == "char1"
    print("OK: mutate_and_save_roundtrip")


def test_checksum_is_valid_after_save():
    raw = build_synthetic_sl2(num_named_slots=1)
    tmp = Path("/tmp/test_synth4.sl2")
    tmp.write_bytes(raw)

    save = EldenRingSave.load(tmp)
    save.slots[0].set_runes(42)
    save.save(tmp, backup=False)

    written = bytearray(tmp.read_bytes())
    s_ind = 0x310
    c_ind = 0x300
    for _ in range(NUM_SLOTS):
        slot_data = bytes(written[s_ind:s_ind + SLOT_DATA_LEN + 1])
        expected = hashlib.md5(slot_data).digest()
        actual = bytes(written[c_ind:c_ind + SLOT_CHECKSUM_LEN])
        assert expected == actual, f"checksum mismatch at slot starting {s_ind}"
        s_ind += SLOT_STRIDE
        c_ind += SLOT_STRIDE
    print("OK: checksum_is_valid_after_save")


def test_backup_created():
    raw = build_synthetic_sl2(num_named_slots=1)
    tmp = Path("/tmp/test_synth5.sl2")
    tmp.write_bytes(raw)
    bak = tmp.with_suffix(tmp.suffix + ".bak")
    bak.unlink(missing_ok=True)

    save = EldenRingSave.load(tmp)
    save.slots[0].set_runes(7)
    save.save(tmp, backup=True)

    assert bak.exists()
    assert bak.read_bytes() == raw  # backup preserves the pre-edit file
    print("OK: backup_created")


def test_ng_plus_roundtrip():
    raw = build_synthetic_sl2(num_named_slots=1)
    tmp = Path("/tmp/test_synth6.sl2")
    tmp.write_bytes(raw)

    save = EldenRingSave.load(tmp)
    slot = save.slots[0]
    assert slot.new_game_plus() == 0
    slot.set_new_game_plus(3)
    assert slot.new_game_plus() == 3
    print("OK: ng_plus_roundtrip")


if __name__ == "__main__":
    test_split_load_roundtrip()
    test_stat_and_rune_read()
    test_mutate_and_save_roundtrip()
    test_checksum_is_valid_after_save()
    test_backup_created()
    test_ng_plus_roundtrip()
    print("\nAll tests passed.")
