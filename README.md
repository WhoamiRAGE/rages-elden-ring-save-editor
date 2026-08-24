# EldenSave

A save editor for **Elden Ring** (PC, `ER0000.sl2`): edit character stats,
runes, and inventory (weapons, armor, talismans, spells, Ashes of War) from
the command line or as a Python library. No Cheat Engine, no GUI required.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

## Why this exists

Unlike Dark Souls Remastered, Elden Ring's PC save files are **not**
AES-encrypted — integrity is enforced purely with MD5 checksums. So the real
work is finding the right byte offsets and recomputing those checksums
correctly. `eldensave` implements that logic from scratch, based on
publicly documented facts about the file format (the same facts that tools
like Cheat Engine tables, ClayAmore/ER-Save-Lib, and
alfizari/Elden-Ring-Save-Editor rely on).

## Features

- Split/merge `.sl2` into header + 10 character slots + regulation block, with full checksum management
- Read/write character name, level, runes, NG+, and all 9 base stats
- List inventory contents
- Add items:
  - **Goods** (Bell Bearings, spells, Golden Seeds/Sacred Tears, etc.) — low risk, file size never changes
  - **Weapons / Armor / Ashes of War** — resizes the internal item table (in place, file size stays fixed); riskier, but battle-tested
- Bundled item database: 951 weapons, 711 armor pieces, 154 talismans, 163 spells (sorceries + incantations), 116 Ashes of War, and the full ~1800-entry goods table
- Automatic `.bak` backup before every write

## Project layout

```
eldensave/
├── eldensave/
│   ├── savefile.py    # core: EldenRingSave / CharacterSlot, byte-level logic
│   ├── items.py       # item ID database loader + name lookup helpers
│   ├── cli.py         # command-line interface
│   └── data/*.json     # bundled item ID tables
├── tests/
│   └── test_savefile.py
├── pyproject.toml
└── LICENSE (MIT)
```

## Installation

```bash
git clone https://github.com/WhoamiRAGE/rages-elden-ring-save-editor.git
cd rages-elden-ring-save-editor
pip install -e .
```

No dependencies beyond the Python 3.10+ standard library. After installing,
the `eldensave` command is available directly; alternatively, run it as a
module with `python -m eldensave.cli` from the repo root.

## Usage

### Reading

| Command | Description |
|---|---|
| `eldensave list SAVE` | List all 10 character slots |
| `eldensave show SAVE --slot N` | Show full stat block for one slot |
| `eldensave inventory SAVE --slot N` | List inventory contents |
| `eldensave debug SAVE` | Structural diagnostics (safe to share — no personal data) |

### Editing

| Command | Description |
|---|---|
| `eldensave set-runes SAVE --slot N --value V` | Set rune count |
| `eldensave set-stat SAVE --slot N --stat vigor --value V` | Set a single stat |
| `eldensave set-name SAVE --slot N --name "..."` | Rename a character |

### Adding items

| Command | Description |
|---|---|
| `add-good --item "Name"` | Add one goods-type item |
| `add-weapon --item "Name"` | Add one weapon |
| `add-armor --item "Name"` | Add one armor piece |
| `add-aow --item "Name"` | Add one Ash of War |
| `add-all-bearings` | Add every Bell Bearing |
| `add-all-talismans` | Add every talisman |
| `add-all-spells` | Add every sorcery + incantation |
| `add-all-weapons [--limit N] [--skip N]` | Add weapons (supports batching) |
| `add-all-armor [--limit N] [--skip N]` | Add armor (supports batching) |
| `add-all-aow [--limit N] [--skip N]` | Add Ashes of War (supports batching) |
| `max-flasks` | Add 30 Golden Seeds + 12 Sacred Tears (enough to fully max flasks) |
| `max-dlc-blessings` | Add 50 Scadutree Fragments + 25 Revered Spirit Ashes (enough to fully max both Shadow of the Erdtree blessings) |

Item names come from `eldensave/items.py` (`WEAPONS_FULL`, `ARMOR_FULL`,
`TALISMANS_FULL`, `SPELLS_FULL`, `AOW_FULL`, `GOODS_FULL`). For anything not
in the database, pass a raw ID directly: `--hex-id AABBCCDD` (4 bytes,
little-endian hex).

### Example

```bash
eldensave list ER0000.sl2
eldensave set-runes ER0000.sl2 --slot 0 --value 500000000
eldensave add-weapon ER0000.sl2 --slot 0 --item "Uchigatana"
eldensave add-all-talismans ER0000.sl2 --slot 0
```

### As a library

```python
from eldensave.savefile import EldenRingSave

save = EldenRingSave.load("ER0000.sl2")
slot = save.slots[0]
print(slot.character_name(), slot.stat("level"), slot.runes())

slot.set_stat("vigor", 60)
slot.set_runes(500_000)
save.save("ER0000.sl2")  # auto .bak backup + checksum recompute
```

## Testing

```bash
python3 tests/test_savefile.py
```

Tests run against synthetic (hand-built, format-accurate) save data. Core
mechanics (name, runes, weapon/armor/Ash of War addition, checksums) have
also been verified against a real save file.

## Known limitations

- Site of Grace unlocks, Whetblades, and Cookbooks use a different
  mechanism (bit flags in a separate region, not inventory items) and
  aren't supported yet.
- PC `.sl2` only — no PS4/`memory.dat` support.
- CLI only, no GUI/TUI.

## Safety

This tool is intended for **offline / single-player use only**. Using a
modified save online can get you banned. Always keep a manual backup in
addition to the automatic `.bak` this tool creates before every write.

## License

MIT — see [LICENSE](LICENSE).
