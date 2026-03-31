# UE3-469-upk-to-psk — UE3 SkeletalMesh Extractor

A GUI tool to extract **SkeletalMesh** assets from early **Unreal Engine 3 prototype (2004, engine version 180)** UPK packages and export them to **PSK (ActorX)** format, compatible with Unreal Engine 3 importers and Blender (via PSK/PSA importer plugin).

> **Why does this exist?**
> Tools like UModel do not support this specific engine version. All internal struct layouts were discovered manually through hex analysis and reverse engineering of the binary format.

---

## Features

- Simple GUI — browse, convert, done
- Exports to `.psk` (ActorX format), importable in UE3 and Blender
- Detailed conversion log with per-mesh stats
- Supports multiple SkeletalMesh exports in a single UPK file
- No external dependencies — pure Python + tkinter

---

## Requirements

- Python 3.x
- tkinter (included with standard Python on Windows)

---

## Usage

```bash
python ue3_psk_extractor.py
```

1. Click **BROWSE** next to *UPK File* and select your `.upk` package
2. Select an output directory (defaults to the same folder as the UPK)
3. Click **CONVERT → PSK**
4. The `.psk` file(s) will appear in the output directory

---

## Tested On

| File | Engine Ver | Meshes | Result |
|------|-----------|--------|--------|
| `Name.upk` | 180 | 1 | ✅ 1633 verts, 93 bones |

---

## Technical Notes

This tool was reverse-engineered specifically for **engine version 180** (early UE3 prototype, 2004).
It may not work on standard UE3 packages (engine ver 400+).

Key differences from standard UE3 discovered during reverse engineering:

- `FName` is **4 bytes** (index only, no instance number field)
- `VJointPos` includes **YSize** (4 floats: Length, XSize, YSize, ZSize)
- `TLazyArray` stores `abs_end_offset` + `count` — fields are **not 4-byte aligned**
- PSK chunk header: `DataSize` comes **before** `DataCount`
- Export table: `serial_size` and `serial_offset` fields are **swapped** vs standard UE3
- Root bone quaternion W must be **negated** per ActorX convention

---

## License

This project is licensed under the **GNU General Public License v3.0**.
See [LICENSE](LICENSE) for details.

You are free to use, modify, and redistribute this tool, but:
- You **cannot** sell it or include it in a commercial product
- Any modified version must also be released under GPL v3 with source code

---

## Author

**Salvo k27** + **Claude AI**
