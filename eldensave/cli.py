"""
eldensave.cli
==============
Minimal command-line interface around eldensave.savefile.EldenRingSave.

Examples
--------
    python -m eldensave.cli list ER0000.sl2
    python -m eldensave.cli show ER0000.sl2 --slot 0
    python -m eldensave.cli set-runes ER0000.sl2 --slot 0 --value 999999
    python -m eldensave.cli set-stat ER0000.sl2 --slot 0 --stat vigor --value 60
    python -m eldensave.cli set-name ER0000.sl2 --slot 0 --name "rage"
    python -m eldensave.cli inventory ER0000.sl2 --slot 0
"""

from __future__ import annotations

import argparse
import sys

from .savefile import EldenRingSave, STAT_OFFSETS, SaveFormatError


def cmd_list(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    for entry in save.summary():
        if entry["empty"]:
            print(f"[slot {entry['slot']}] (empty)")
        else:
            print(
                f"[slot {entry['slot']}] {entry['name']!r}  "
                f"lvl {entry['level']}  runes {entry['runes']}  "
                f"class {entry['class']}  NG+{entry['ng_plus']}"
            )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    if slot.is_empty():
        print(f"Slot {args.slot} is empty.")
        return 0
    print(f"Name:    {slot.character_name()}")
    print(f"Runes:   {slot.runes()}")
    print(f"NG+:     {slot.new_game_plus()}")
    for stat, value in slot.all_stats().items():
        print(f"{stat:22s}: {value}")
    return 0


def cmd_set_runes(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    slot.set_runes(args.value)
    save.save(args.path)
    print(f"Slot {args.slot}: runes -> {args.value}")
    return 0


def cmd_set_stat(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    slot.set_stat(args.stat, args.value)
    save.save(args.path)
    print(f"Slot {args.slot}: {args.stat} -> {args.value}")
    return 0


def cmd_set_name(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    slot.set_character_name(args.name)
    save.save(args.path)
    print(f"Slot {args.slot}: name -> {args.name!r}")
    return 0


def cmd_debug(args: argparse.Namespace) -> int:
    """Prints structural diagnostics only -- no full character data dump --
    so it's safe to paste the output for troubleshooting."""
    import os
    from .savefile import (
        GA_ITEM_LIST_START, GA_TO_MAGIC, MAGIC_TO_NAME, NAME_LEN_BYTES,
        NG_PLUS_PATTERN, MAGIC_TO_NG_PATTERN_DISTANCE, HEADER_SIZE, SLOT_STRIDE,
    )

    path = args.path
    size = os.path.getsize(path)
    print(f"file: {path}")
    print(f"size: {size} bytes (expected minimum: {HEADER_SIZE + 10 * SLOT_STRIDE})")
    print()

    save = EldenRingSave.load(path)
    for slot in save.slots:
        print(f"--- slot {slot.index} ---")
        if slot.is_empty():
            # even "empty" needs the ga-walk to have run to say so; show why
            ga_end = slot._walk_ga_items()
            magic = ga_end + GA_TO_MAGIC
            name_off = magic + MAGIC_TO_NAME
            raw = bytes(slot.data[name_off:name_off + NAME_LEN_BYTES])
            print(f"  ga_end={ga_end} (0x{ga_end:X})  magic={magic} (0x{magic:X})")
            print(f"  name bytes at 0x{name_off:X}: {raw.hex()}  -> decoded: {raw.decode('utf-16-le', errors='replace')!r}")
            print("  => treated as EMPTY (no name decoded)")
            continue

        ga_end = slot._walk_ga_items()
        magic = ga_end + GA_TO_MAGIC
        name_off = magic + MAGIC_TO_NAME
        raw_name = bytes(slot.data[name_off:name_off + NAME_LEN_BYTES])
        print(f"  ga_end={ga_end} (0x{ga_end:X})  magic={magic} (0x{magic:X})")
        print(f"  name bytes at 0x{name_off:X}: {raw_name.hex()}")
        print(f"  decoded name: {slot.character_name()!r}")
        print(f"  level={slot.stat('level')}  runes={slot.runes()}")
        vals = {k: slot.stat(k) for k in ("vigor", "mind", "endurance", "strength", "dexterity", "intelligence", "faith", "arcane")}
        print(f"  raw stat bytes: {vals}")
        pattern_pos = slot.data.find(NG_PLUS_PATTERN)
        print(f"  NG+ pattern found at: {pattern_pos} (offset from magic: {pattern_pos - magic if pattern_pos != -1 else 'N/A'})")
        print(f"  NG+ value: {slot.new_game_plus()}")
        items = slot.ga_items()
        print(f"  non-empty ga-item handles found: {len(items)}")
    return 0


def cmd_find_bytes(args: argparse.Namespace) -> int:
    """Search a slot's raw data for a sequence of single-byte values
    (e.g. known stat values in order) and report every offset it occurs at,
    plus its distance from the computed 'magic' anchor. Safe to share --
    doesn't print any character name or unrelated data."""
    from .savefile import GA_TO_MAGIC

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    values = [int(x.strip()) for x in args.bytes.split(",")]
    for v in values:
        if not (0 <= v <= 255):
            print(f"error: {v} is not a valid single byte (0-255)", file=sys.stderr)
            return 1
    pattern = bytes(values)

    ga_end = slot._walk_ga_items()
    magic = ga_end + GA_TO_MAGIC

    data = bytes(slot.data)
    found = []
    start = 0
    while True:
        idx = data.find(pattern, start)
        if idx == -1:
            break
        found.append(idx)
        start = idx + 1

    print(f"magic anchor = {magic} (0x{magic:X})")
    print(f"searching for bytes: {values}")
    print(f"found {len(found)} occurrence(s):")
    for off in found:
        print(f"  offset {off} (0x{off:X})  distance from magic: {off - magic}")
    return 0


def cmd_add_good(args: argparse.Namespace) -> int:
    from .items import BELL_BEARINGS, GOODS_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    try:
        hex_id = find(args.item, GOODS_FULL)
    except KeyError:
        if args.hex_id:
            hex_id = args.hex_id
        else:
            print(f"error: unknown item name {args.item!r}", file=sys.stderr)
            return 1

    slot.add_good(item_id_bytes(hex_id), quantity=args.quantity)
    save.save(args.path)
    print(f"Slot {args.slot}: added {args.item!r} x{args.quantity}")
    return 0


def cmd_add_all_bearings(args: argparse.Namespace) -> int:
    from .items import BELL_BEARINGS, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = [], []
    for name, hex_id in BELL_BEARINGS.items():
        try:
            slot.add_good(item_id_bytes(hex_id), quantity=1)
            added.append(name)
        except Exception as e:
            failed.append((name, str(e)))

    save.save(args.path)
    print(f"Slot {args.slot}: added {len(added)} Bell Bearing(s)")
    if failed:
        print(f"  {len(failed)} failed:")
        for name, err in failed:
            print(f"    {name}: {err}")
    return 0


def cmd_add_armor(args: argparse.Namespace) -> int:
    from .items import ARMOR_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    try:
        hex_id = find(args.item, ARMOR_FULL)
    except KeyError:
        if args.hex_id:
            hex_id = args.hex_id
        else:
            print(f"error: unknown armor {args.item!r}", file=sys.stderr)
            return 1

    slot.add_armor(item_id_bytes(hex_id))
    save.save(args.path)
    print(f"Slot {args.slot}: added armor {args.item!r}")
    return 0


def cmd_add_raging_wolf_set(args: argparse.Namespace) -> int:
    from .items import ARMOR, RAGING_WOLF_SET, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = [], []
    for name in RAGING_WOLF_SET:
        try:
            slot.add_armor(item_id_bytes(ARMOR[name]))
            added.append(name)
        except Exception as e:
            failed.append((name, str(e)))
            break  # ga-table state may be inconsistent after a failure; stop here

    save.save(args.path)
    print(f"Slot {args.slot}: added {len(added)} piece(s): {', '.join(added)}")
    if failed:
        print(f"  failed on: {failed[0][0]}: {failed[0][1]}")
    return 0


def cmd_add_weapon(args: argparse.Namespace) -> int:
    from .items import WEAPONS_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    try:
        hex_id = find(args.item, WEAPONS_FULL)
    except KeyError:
        if args.hex_id:
            hex_id = args.hex_id
        else:
            print(f"error: unknown weapon name {args.item!r}", file=sys.stderr)
            return 1

    slot.add_weapon(item_id_bytes(hex_id))
    save.save(args.path)
    print(f"Slot {args.slot}: added weapon {args.item!r}")
    return 0


def cmd_add_all_weapons(args: argparse.Namespace) -> int:
    from .items import WEAPONS_FULL, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = 0, []
    items = list(WEAPONS_FULL.items())
    if args.skip:
        items = items[args.skip:]
    if args.limit:
        items = items[:args.limit]
    for name, hex_id in items:
        try:
            slot.add_weapon(item_id_bytes(hex_id))
            added += 1
        except Exception as e:
            failed.append((name, str(e)))
            if args.stop_on_error:
                break

    save.save(args.path)
    print(f"Slot {args.slot}: added {added}/{len(items)} weapons (database has {len(WEAPONS_FULL)} total)")
    if failed:
        print(f"  {len(failed)} failed (showing first 10):")
        for name, err in failed[:10]:
            print(f"    {name}: {err}")
    return 0


def cmd_add_all_armor(args: argparse.Namespace) -> int:
    from .items import ARMOR_FULL, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = 0, []
    items = list(ARMOR_FULL.items())
    if args.skip:
        items = items[args.skip:]
    if args.limit:
        items = items[:args.limit]
    for name, hex_id in items:
        try:
            slot.add_armor(item_id_bytes(hex_id))
            added += 1
        except Exception as e:
            failed.append((name, str(e)))
            if args.stop_on_error:
                break

    save.save(args.path)
    print(f"Slot {args.slot}: added {added}/{len(items)} armor pieces (database has {len(ARMOR_FULL)} total)")
    if failed:
        print(f"  {len(failed)} failed (showing first 10):")
        for name, err in failed[:10]:
            print(f"    {name}: {err}")
    return 0


def cmd_add_aow(args: argparse.Namespace) -> int:
    from .items import AOW_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    try:
        hex_id = find(args.item, AOW_FULL)
    except KeyError:
        if args.hex_id:
            hex_id = args.hex_id
        else:
            print(f"error: unknown Ash of War {args.item!r}", file=sys.stderr)
            return 1

    slot.add_aow(item_id_bytes(hex_id))
    save.save(args.path)
    print(f"Slot {args.slot}: added Ash of War {args.item!r}")
    return 0


def cmd_add_all_aow(args: argparse.Namespace) -> int:
    from .items import AOW_FULL, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = 0, []
    items = list(AOW_FULL.items())
    if args.skip:
        items = items[args.skip:]
    if args.limit:
        items = items[:args.limit]
    for name, hex_id in items:
        try:
            slot.add_aow(item_id_bytes(hex_id))
            added += 1
        except Exception as e:
            failed.append((name, str(e)))
            if args.stop_on_error:
                break

    save.save(args.path)
    print(f"Slot {args.slot}: added {added}/{len(items)} Ashes of War (database has {len(AOW_FULL)} total)")
    if failed:
        print(f"  {len(failed)} failed (showing first 10):")
        for name, err in failed[:10]:
            print(f"    {name}: {err}")
    return 0


def cmd_max_flasks(args: argparse.Namespace) -> int:
    """30 Golden Seeds = max flask charge count (14 total, 10 upgrades).
    12 Sacred Tears = max flask potency (+12). Confirmed current numbers,
    verified via web search rather than assumed from memory."""
    from .items import GOODS_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    golden_seed = item_id_bytes(find("Golden Seed", GOODS_FULL))
    sacred_tear = item_id_bytes(find("Sacred Tear", GOODS_FULL))

    slot.add_good(golden_seed, quantity=30)
    slot.add_good(sacred_tear, quantity=12)
    save.save(args.path)
    print(f"Slot {args.slot}: 30x Golden Seed, 12x Sacred Tear added "
          f"(enough to max flask count to 14 and potency to +12 at any Site of Grace)")
    return 0


def cmd_add_all_spells(args: argparse.Namespace) -> int:
    from .items import SPELLS_FULL, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = 0, []
    for name, hex_id in SPELLS_FULL.items():
        try:
            slot.add_good(item_id_bytes(hex_id), quantity=1)
            added += 1
        except Exception as e:
            failed.append((name, str(e)))

    save.save(args.path)
    print(f"Slot {args.slot}: added {added}/{len(SPELLS_FULL)} sorceries/incantations")
    if failed:
        print(f"  {len(failed)} failed (showing first 10):")
        for name, err in failed[:10]:
            print(f"    {name}: {err}")
    return 0


def cmd_add_all_talismans(args: argparse.Namespace) -> int:
    from .items import TALISMANS_FULL, item_id_bytes

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    added, failed = 0, []
    for name, hex_id in TALISMANS_FULL.items():
        try:
            slot.add_good(item_id_bytes(hex_id), quantity=1)
            added += 1
        except Exception as e:
            failed.append((name, str(e)))

    save.save(args.path)
    print(f"Slot {args.slot}: added {added}/{len(TALISMANS_FULL)} talismans")
    if failed:
        print(f"  {len(failed)} failed (showing first 10):")
        for name, err in failed[:10]:
            print(f"    {name}: {err}")
    return 0


def cmd_inventory(args: argparse.Namespace) -> int:
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]
    items = slot.inventory()
    if not items:
        print("No inventory items found (or parsing anchor is off for this save version).")
    for item in items:
        print(f"{item.category:10s} handle=0x{item.handle:08X} qty={item.quantity} idx={item.index}")
    return 0



def cmd_max_dlc_blessings(args: argparse.Namespace) -> int:
    """50 Scadutree Fragments = max Scadutree Blessing (level 20).
    25 Revered Spirit Ashes = max Revered Spirit Ash Blessing (level 10).
    Both confirmed current numbers, verified via web search."""
    from .items import GOODS_FULL, item_id_bytes, find
    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    scadutree_fragment = item_id_bytes(find("Scadutree Fragment", GOODS_FULL))
    revered_spirit_ash = item_id_bytes(find("Revered Spirit Ash", GOODS_FULL))

    slot.add_good(scadutree_fragment, quantity=50)
    slot.add_good(revered_spirit_ash, quantity=25)

    save.save(args.path)
    print(f"Slot {args.slot}: 50x Scadutree Fragment, 25x Revered Spirit Ash added "
          f"(enough to max both Shadow of the Erdtree blessings).")
    return 0

def cmd_add_somber_stones(args: argparse.Namespace) -> int:
    """Adds every Somber Smithing Stone tier (1-9 + Ancient Dragon),
    quantity each controlled by --quantity (default 100)."""
    from .items import GOODS_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    names = [f"Somber Smithing Stone [{i}]" for i in range(1, 10)] + ["Somber Ancient Dragon Smithing Stone"]

    added, failed = [], []
    for name in names:
        try:
            slot.add_good(item_id_bytes(find(name, GOODS_FULL)), quantity=args.quantity)
            added.append(name)
        except Exception as e:
            failed.append((name, str(e)))

    save.save(args.path)
    print(f"Slot {args.slot}: added {args.quantity}x each of {len(added)} Somber Smithing Stone types")
    if failed:
        print(f"  {len(failed)} failed:")
        for name, err in failed:
            print(f"    {name}: {err}")
    return 0


def cmd_add_smithing_stones(args: argparse.Namespace) -> int:
    """Adds every regular Smithing Stone tier (1-8 + Ancient Dragon),
    quantity each controlled by --quantity (default 100)."""
    from .items import GOODS_FULL, item_id_bytes, find

    save = EldenRingSave.load(args.path)
    slot = save.slots[args.slot]

    names = [f"Smithing Stone [{i}]" for i in range(1, 9)] + ["Ancient Dragon Smithing Stone"]

    added, failed = [], []
    for name in names:
        try:
            slot.add_good(item_id_bytes(find(name, GOODS_FULL)), quantity=args.quantity)
            added.append(name)
        except Exception as e:
            failed.append((name, str(e)))

    save.save(args.path)
    print(f"Slot {args.slot}: added {args.quantity}x each of {len(added)} Smithing Stone types")
    if failed:
        print(f"  {len(failed)} failed:")
        for name, err in failed:
            print(f"    {name}: {err}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eldensave", description="Elden Ring .sl2 save editor (core CLI)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List all 10 character slots")
    p_list.add_argument("path")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="Show full stat block for one slot")
    p_show.add_argument("path")
    p_show.add_argument("--slot", type=int, required=True)
    p_show.set_defaults(func=cmd_show)

    p_runes = sub.add_parser("set-runes", help="Set rune count for one slot")
    p_runes.add_argument("path")
    p_runes.add_argument("--slot", type=int, required=True)
    p_runes.add_argument("--value", type=int, required=True)
    p_runes.set_defaults(func=cmd_set_runes)

    p_stat = sub.add_parser("set-stat", help="Set a single stat for one slot")
    p_stat.add_argument("path")
    p_stat.add_argument("--slot", type=int, required=True)
    p_stat.add_argument("--stat", choices=sorted(STAT_OFFSETS), required=True)
    p_stat.add_argument("--value", type=int, required=True)
    p_stat.set_defaults(func=cmd_set_stat)

    p_name = sub.add_parser("set-name", help="Rename a character slot")
    p_name.add_argument("path")
    p_name.add_argument("--slot", type=int, required=True)
    p_name.add_argument("--name", required=True)
    p_name.set_defaults(func=cmd_set_name)

    p_inv = sub.add_parser("inventory", help="List parsed inventory items for one slot")
    p_inv.add_argument("path")
    p_inv.add_argument("--slot", type=int, required=True)
    p_inv.set_defaults(func=cmd_inventory)

    p_debug = sub.add_parser("debug", help="Print structural diagnostics (safe to paste/share)")
    p_debug.add_argument("path")
    p_debug.set_defaults(func=cmd_debug)

    p_add_good = sub.add_parser("add-good", help="Add a consumable/key item (e.g. a Bell Bearing) to inventory")
    p_add_good.add_argument("path")
    p_add_good.add_argument("--slot", type=int, required=True)
    p_add_good.add_argument("--item", required=True, help="Known item name (see eldensave.items.BELL_BEARINGS)")
    p_add_good.add_argument("--hex-id", help="Raw 4-byte little-endian hex item ID, if --item isn't in the database")
    p_add_good.add_argument("--quantity", type=int, default=1)
    p_add_good.set_defaults(func=cmd_add_good)

    p_add_all_bearings = sub.add_parser("add-all-bearings", help="Add every known Bell Bearing in one pass")
    p_add_all_bearings.add_argument("path")
    p_add_all_bearings.add_argument("--slot", type=int, required=True)
    p_add_all_bearings.set_defaults(func=cmd_add_all_bearings)

    p_add_armor = sub.add_parser("add-armor", help="Add a single armor piece to inventory")
    p_add_armor.add_argument("path")
    p_add_armor.add_argument("--slot", type=int, required=True)
    p_add_armor.add_argument("--item", required=True, help="Known armor name (see eldensave.items.ARMOR)")
    p_add_armor.add_argument("--hex-id", help="Raw 4-byte little-endian hex item ID, if --item isn't in the database")
    p_add_armor.set_defaults(func=cmd_add_armor)

    p_add_rw_set = sub.add_parser("add-raging-wolf-set", help="Add all 4 pieces of the Raging Wolf armor set")
    p_add_rw_set.add_argument("path")
    p_add_rw_set.add_argument("--slot", type=int, required=True)
    p_add_rw_set.set_defaults(func=cmd_add_raging_wolf_set)

    p_add_weapon = sub.add_parser("add-weapon", help="Add a weapon to inventory (resizes the ga-item table)")
    p_add_weapon.add_argument("path")
    p_add_weapon.add_argument("--slot", type=int, required=True)
    p_add_weapon.add_argument("--item", required=True, help="Known weapon name (see eldensave.items.WEAPONS)")
    p_add_weapon.add_argument("--hex-id", help="Raw 4-byte little-endian hex item ID, if --item isn't in the database")
    p_add_weapon.set_defaults(func=cmd_add_weapon)

    p_add_all_weapons = sub.add_parser("add-all-weapons", help="Add every known weapon (951 items, slow, resizes ga-table repeatedly)")
    p_add_all_weapons.add_argument("path")
    p_add_all_weapons.add_argument("--slot", type=int, required=True)
    p_add_all_weapons.add_argument("--stop-on-error", action="store_true", help="Stop at the first failure instead of skipping and continuing")
    p_add_all_weapons.add_argument("--limit", type=int, help="Only add the first N weapons (for safe incremental testing)")
    p_add_all_weapons.add_argument("--skip", type=int, help="Skip the first N weapons (e.g. ones already added in a previous run)")
    p_add_all_weapons.set_defaults(func=cmd_add_all_weapons)

    p_add_all_armor = sub.add_parser("add-all-armor", help="Add every known armor piece (711 items, resizes ga-table repeatedly)")
    p_add_all_armor.add_argument("path")
    p_add_all_armor.add_argument("--slot", type=int, required=True)
    p_add_all_armor.add_argument("--stop-on-error", action="store_true")
    p_add_all_armor.add_argument("--limit", type=int, help="Only add the first N pieces (for safe incremental testing)")
    p_add_all_armor.add_argument("--skip", type=int, help="Skip the first N pieces (e.g. ones already added in a previous run)")
    p_add_all_armor.set_defaults(func=cmd_add_all_armor)

    p_add_all_talismans = sub.add_parser("add-all-talismans", help="Add every known talisman (154 items)")
    p_add_all_talismans.add_argument("path")
    p_add_all_talismans.add_argument("--slot", type=int, required=True)
    p_add_all_talismans.set_defaults(func=cmd_add_all_talismans)

    p_add_all_spells = sub.add_parser("add-all-spells", help="Add every known sorcery and incantation (163 items)")
    p_add_all_spells.add_argument("path")
    p_add_all_spells.add_argument("--slot", type=int, required=True)
    p_add_all_spells.set_defaults(func=cmd_add_all_spells)

    p_max_flasks = sub.add_parser("max-flasks", help="Add 30 Golden Seeds + 12 Sacred Tears (enough to fully max flasks)")
    p_max_flasks.add_argument("path")
    p_max_flasks.add_argument("--slot", type=int, required=True)
    p_max_flasks.set_defaults(func=cmd_max_flasks)

    p_add_aow = sub.add_parser("add-aow", help="Add a single Ash of War to inventory")
    p_add_aow.add_argument("path")
    p_add_aow.add_argument("--slot", type=int, required=True)
    p_add_aow.add_argument("--item", required=True, help="Known Ash of War name (see eldensave.items.AOW_FULL)")
    p_add_aow.add_argument("--hex-id", help="Raw 4-byte little-endian hex item ID, if --item isn't in the database")
    p_add_aow.set_defaults(func=cmd_add_aow)

    p_add_all_aow = sub.add_parser("add-all-aow", help="Add every known Ash of War (116 items)")
    p_add_all_aow.add_argument("path")
    p_add_all_aow.add_argument("--slot", type=int, required=True)
    p_add_all_aow.add_argument("--stop-on-error", action="store_true")
    p_add_all_aow.add_argument("--limit", type=int, help="Only add the first N (for safe incremental testing)")
    p_add_all_aow.add_argument("--skip", type=int, help="Skip the first N (e.g. already added in a previous run)")
    p_add_all_aow.set_defaults(func=cmd_add_all_aow)

    p_dlc_blessings = sub.add_parser("max-dlc-blessings", help="Add max DLC blessings (50 Scadutree, 20 Spirit Ash)")
    p_dlc_blessings.add_argument("path")
    p_dlc_blessings.add_argument("--slot", type=int, required=True)
    p_dlc_blessings.set_defaults(func=cmd_max_dlc_blessings)

    p_somber = sub.add_parser("add-somber-stones", help="Add every Somber Smithing Stone type (1-9 + Ancient Dragon)")
    p_somber.add_argument("path")
    p_somber.add_argument("--slot", type=int, required=True)
    p_somber.add_argument("--quantity", type=int, default=100)
    p_somber.set_defaults(func=cmd_add_somber_stones)

    p_smithing = sub.add_parser("add-smithing-stones", help="Add every regular Smithing Stone type (1-8 + Ancient Dragon)")
    p_smithing.add_argument("path")
    p_smithing.add_argument("--slot", type=int, required=True)
    p_smithing.add_argument("--quantity", type=int, default=100)
    p_smithing.set_defaults(func=cmd_add_smithing_stones)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SaveFormatError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (ValueError, KeyError, IndexError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
