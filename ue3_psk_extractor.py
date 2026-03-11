#!/usr/bin/env python3
"""
UE3 SkeletalMesh Extractor to PSK
Early Unreal Engine 3 (2004, engine ver 180) — UPK → PSK converter
GUI wrapper with dark industrial aesthetic
"""

import struct
import sys
import os
import io
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import time

# ═══════════════════════════════════════════════════════════════════════════════
#  CONVERTER ENGINE  (identical logic to upk_to_psk.py)
# ═══════════════════════════════════════════════════════════════════════════════

def _r(data, fmt, off):
    return struct.unpack_from(fmt, data, off)

def i32(data, off): return _r(data, '<i', off)[0]
def u32(data, off): return _r(data, '<I', off)[0]
def f32(data, off): return _r(data, '<f', off)[0]
def u16(data, off): return _r(data, '<H', off)[0]

def parse_upk(data):
    tag = u32(data, 0)
    assert tag == 0x9E2A83C1, f"Bad UPK tag: {tag:#x}"
    eng = _r(data, '<H', 4)[0]
    lic = _r(data, '<H', 6)[0]
    name_count    = i32(data, 12)
    name_offset   = u32(data, 16)
    export_count  = i32(data, 20)
    export_offset = u32(data, 24)
    import_count  = i32(data, 28)
    import_offset = u32(data, 32)

    pos = name_offset
    names = []
    for _ in range(name_count):
        slen = i32(data, pos); pos += 4
        name = data[pos:pos+slen-1].decode('latin-1'); pos += slen
        pos += 4
        names.append(name)

    pos = import_offset
    imports = []
    for _ in range(import_count):
        cp = i32(data, pos); cn = i32(data, pos+4)
        outer = i32(data, pos+8); on = i32(data, pos+12)
        pos += 16
        obj_name = names[on] if 0 <= on < len(names) else f'?{on}'
        imports.append(obj_name)

    pos = export_offset
    exports = []
    for i in range(export_count):
        class_idx   = i32(data, pos)
        super_idx   = i32(data, pos+4)
        outer_idx   = i32(data, pos+8)
        name_idx    = i32(data, pos+12)
        obj_flags   = u32(data, pos+16)
        serial_size = i32(data, pos+20)
        serial_off  = i32(data, pos+24)
        pos += 32
        obj_name = names[name_idx] if 0 <= name_idx < len(names) else f'?{name_idx}'
        if class_idx < 0:
            ii = -class_idx - 1
            cls_name = imports[ii] if 0 <= ii < len(imports) else f'imp[{ii}]'
        elif class_idx == 0:
            cls_name = 'Class'
        else:
            cls_name = f'exp[{class_idx-1}]'
        exports.append({'name': obj_name, 'class': cls_name,
                        'offset': serial_off, 'size': serial_size, 'index': i})

    return names, imports, exports, eng, lic


def read_lazy_array(data, abs_field_off, elem_size):
    abs_end    = u32(data, abs_field_off)
    count      = i32(data, abs_field_off + 4)
    data_start = abs_field_off + 8
    expected_end = data_start + count * elem_size
    if expected_end != abs_end:
        raise ValueError(
            f"TLazyArray mismatch at {abs_field_off:#x}: abs_end={abs_end:#x} "
            f"but computed end={expected_end:#x} (count={count}, elem={elem_size})")
    raw = data[data_start:data_start + count * elem_size]
    return count, raw, data_start


def find_lazy_array_backwards(data, end_offset, elem_size, skel_start, label):
    low_byte = end_offset & 0xFF
    for off in range(end_offset - 8, skel_start, -1):
        if data[off] != low_byte:
            continue
        v = struct.unpack_from('<I', data, off)[0]
        if v != end_offset:
            continue
        cnt = struct.unpack_from('<i', data, off + 4)[0]
        if cnt <= 0 or cnt > 200000:
            continue
        data_start = off + 8
        if data_start + cnt * elem_size == end_offset:
            return off, cnt, data[data_start:data_start + cnt * elem_size]
    raise ValueError(f"Cannot find TLazyArray '{label}' (elem={elem_size}) ending at {end_offset:#x}")


def parse_skeletal_mesh(data, skel_offset, skel_size):
    skel_end = skel_offset + skel_size
    pos = skel_offset

    none_idx = i32(data, pos)
    assert none_idx == 0, f"Expected FName None, got idx={none_idx}"
    pos += 8

    pos += 24   # FBoxSphereBounds (2×FVector, no radius)

    mat_count = i32(data, pos); pos += 4
    pos += mat_count * 4

    pos += 12   # Origin FVector
    pos += 12   # RotOrigin FRotator

    # FMeshBone: FName(4)+flags(4)+FQuat(16)+FVector(12)+4×float(16)+nch(4)+par(4) = 60 bytes
    bone_count = i32(data, pos); pos += 4
    bones = []
    for b in range(bone_count):
        n_idx = i32(data, pos)
        flags = u32(data, pos+4)
        qx=f32(data,pos+8);  qy=f32(data,pos+12); qz=f32(data,pos+16); qw=f32(data,pos+20)
        px=f32(data,pos+24); py=f32(data,pos+28); pz=f32(data,pos+32)
        blen=f32(data,pos+36); xs=f32(data,pos+40); ys=f32(data,pos+44); zs=f32(data,pos+48)
        nch=i32(data,pos+52); par=i32(data,pos+56)
        pos += 60
        bones.append({'name': n_idx, 'flags': flags,
                      'quat': (qx,qy,qz,qw), 'pos': (px,py,pz),
                      'nch': nch, 'par': par if b > 0 else -1})

    pos += 4   # SkeletalDepth
    lod_count = i32(data, pos); pos += 4

    # Points
    points_abs_field = None
    for off in range(skel_end - 8, pos, -4):
        if u32(data, off) == skel_end:
            cnt = i32(data, off+4)
            if cnt > 0 and off + 8 + cnt*12 == skel_end:
                points_abs_field = off; break
    if points_abs_field is None:
        raise ValueError("Cannot find Points TLazyArray")
    _, pt_raw, _ = read_lazy_array(data, points_abs_field, 12)
    n_points = len(pt_raw) // 12
    points = [struct.unpack_from('<fff', pt_raw, i*12) for i in range(n_points)]

    # Faces
    faces_abs_field, n_faces, face_raw = find_lazy_array_backwards(
        data, points_abs_field, 8, pos, 'Faces')
    faces = []
    for i in range(n_faces):
        w0=struct.unpack_from('<H',face_raw,i*8)[0]
        w1=struct.unpack_from('<H',face_raw,i*8+2)[0]
        w2=struct.unpack_from('<H',face_raw,i*8+4)[0]
        mat=struct.unpack_from('<H',face_raw,i*8+6)[0]
        faces.append((w0,w1,w2,mat))

    # Wedges
    wedges_abs_field, n_wedges, wedge_raw = find_lazy_array_backwards(
        data, faces_abs_field, 10, pos, 'Wedges')
    wedges = []
    for i in range(n_wedges):
        vi = struct.unpack_from('<H', wedge_raw, i*10)[0]
        u  = struct.unpack_from('<f', wedge_raw, i*10+2)[0]
        v  = struct.unpack_from('<f', wedge_raw, i*10+6)[0]
        wedges.append((vi, u, v))

    # Influences
    infl_abs_field, n_infl, infl_raw = find_lazy_array_backwards(
        data, wedges_abs_field, 8, pos, 'Influences')
    influences = []
    for i in range(n_infl):
        w  = struct.unpack_from('<f', infl_raw, i*8)[0]
        vi = struct.unpack_from('<H', infl_raw, i*8+4)[0]
        bi = struct.unpack_from('<H', infl_raw, i*8+6)[0]
        influences.append((w, vi, bi))

    return bones, points, wedges, faces, influences


def write_psk_chunk(out, tag, data_bytes, item_count, item_size):
    header = struct.pack('<20sIII',
        tag.ljust(20, b'\x00'),
        1985948786,
        item_size,
        item_count)
    out.write(header)
    out.write(data_bytes)


def export_psk(bones, points, wedges, faces, influences, names, out_path):
    buf = io.BytesIO()
    write_psk_chunk(buf, b'ACTRHEAD', b'', 0, 0)

    pnts = b''.join(struct.pack('<fff', x, y, z) for (x,y,z) in points)
    write_psk_chunk(buf, b'PNTS0000', pnts, len(points), 12)

    vtxw = b''.join(struct.pack('<IffBBH', vi, u, v, 0, 0, 0) for (vi,u,v) in wedges)
    write_psk_chunk(buf, b'VTXW0000', vtxw, len(wedges), 16)

    face_bytes = b''.join(struct.pack('<HHHBBI', w0,w1,w2,mat&0xFF,0,0) for (w0,w1,w2,mat) in faces)
    write_psk_chunk(buf, b'FACE0000', face_bytes, len(faces), 12)

    mat_indices = sorted(set(f[3] for f in faces))
    matt = b''
    for mat_idx in mat_indices:
        mn = f'Material_{mat_idx}'.encode('utf-8')[:27].ljust(28, b'\x00')
        matt += struct.pack('<28sIIIII', mn, 0, 0, 0, 0, 0)
        matt += b'\x00' * (88 - 28 - 20)
    write_psk_chunk(buf, b'MATT0000', matt, len(mat_indices), 88)

    refskelt = b''
    for b_idx, bone in enumerate(bones):
        b_name = names[bone['name']] if 0 <= bone['name'] < len(names) else f'Bone_{b_idx}'
        b_name_bytes = b_name.encode('utf-8')[:63].ljust(64, b'\x00')
        px,py,pz = bone['pos']
        qx,qy,qz,qw = bone['quat']
        par = bone['par'] if bone['par'] >= 0 else 0
        if b_idx == 0:
            qw = -qw
        refskelt += struct.pack('<64sIII', b_name_bytes, 0, bone['nch'], par)
        refskelt += struct.pack('<ffff', qx, qy, qz, qw)
        refskelt += struct.pack('<fff', px, py, pz)
        refskelt += struct.pack('<ffff', 1.0, 1.0, 1.0, 1.0)
    write_psk_chunk(buf, b'REFSKELT', refskelt, len(bones), 120)

    raww = b''.join(struct.pack('<fii', w, vi, bi) for (w,vi,bi) in influences)
    write_psk_chunk(buf, b'RAWWEIGHTS', raww, len(influences), 12)

    with open(out_path, 'wb') as f:
        f.write(buf.getvalue())


def convert_upk(upk_path, out_dir, log_callback, done_callback):
    """Run conversion in a thread. Calls log_callback(msg) and done_callback(success, results)."""
    results = []
    try:
        with open(upk_path, 'rb') as f:
            data = f.read()

        log_callback(f"  File size: {len(data):,} bytes")
        names, imports, exports, eng, lic = parse_upk(data)
        log_callback(f"  Engine version: {eng}   Licensee: {lic}")
        log_callback(f"  Names: {len(names)}   Exports: {len(exports)}   Imports: {len(imports)}")

        skel_exports = [e for e in exports if e['class'] == 'SkeletalMesh' and e['size'] > 0]
        if not skel_exports:
            log_callback("  [!] No SkeletalMesh exports found in this package.")
            done_callback(False, [])
            return

        log_callback(f"  Found {len(skel_exports)} SkeletalMesh export(s)")

        for exp in skel_exports:
            log_callback(f"\n  ► Exporting: {exp['name']}")
            log_callback(f"    offset={exp['offset']:#x}  size={exp['size']:,} bytes")
            try:
                bones, points, wedges, faces, influences = parse_skeletal_mesh(
                    data, exp['offset'], exp['size'])
                out_path = os.path.join(out_dir, f"{exp['name']}.psk")
                export_psk(bones, points, wedges, faces, influences, names, out_path)
                log_callback(f"    ✓ Points:     {len(points)}")
                log_callback(f"    ✓ Wedges:     {len(wedges)}")
                log_callback(f"    ✓ Faces:      {len(faces)}")
                log_callback(f"    ✓ Bones:      {len(bones)}")
                log_callback(f"    ✓ Influences: {len(influences)}")
                log_callback(f"    ✓ Saved: {out_path}")
                results.append({'name': exp['name'], 'path': out_path,
                                 'bones': len(bones), 'faces': len(faces)})
            except Exception as e:
                log_callback(f"    ✗ ERROR: {e}")
                import traceback
                for line in traceback.format_exc().splitlines():
                    log_callback(f"      {line}")

        done_callback(True, results)

    except Exception as e:
        log_callback(f"  ✗ FATAL ERROR: {e}")
        import traceback
        for line in traceback.format_exc().splitlines():
            log_callback(f"    {line}")
        done_callback(False, [])


# ═══════════════════════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════════════════════

# Dark industrial color palette
BG       = "#0f0f0f"
BG2      = "#161616"
BG3      = "#1e1e1e"
PANEL    = "#111111"
BORDER   = "#2a2a2a"
ACCENT   = "#c8502a"       # burnt orange — classic modding tool color
ACCENT2  = "#e06030"
DIM      = "#444444"
TEXT     = "#d4cfc8"
TEXT2    = "#888880"
TEXT3    = "#555550"
GREEN    = "#5a9e5a"
RED      = "#9e3a3a"
MONO     = "Consolas" if sys.platform == "win32" else "Courier New"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("UE3 SkeletalMesh Extractor → PSK")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(680, 520)

        self._upk_path   = tk.StringVar()
        self._out_dir    = tk.StringVar()
        self._busy       = False
        self._anim_frame = 0

        self._build_ui()
        self.update_idletasks()
        w, h = 760, 620
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Title bar ────────────────────────────────────────────────────────
        title_bar = tk.Frame(self, bg=BG, pady=0)
        title_bar.pack(fill="x", padx=0, pady=0)

        tk.Frame(title_bar, bg=ACCENT, height=2).pack(fill="x")

        header = tk.Frame(title_bar, bg=BG, padx=18, pady=14)
        header.pack(fill="x")

        # UE logo-ish badge
        badge = tk.Frame(header, bg=ACCENT, width=38, height=38)
        badge.pack(side="left")
        badge.pack_propagate(False)
        tk.Label(badge, text="UE", font=(MONO, 13, "bold"), bg=ACCENT,
                 fg="#fff").pack(expand=True)

        title_txt = tk.Frame(header, bg=BG, padx=12)
        title_txt.pack(side="left")
        tk.Label(title_txt, text="SkeletalMesh Extractor",
                 font=("Georgia", 16, "bold"), bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(title_txt, text="Unreal Engine 3  ·  2004 prototype  (engine ver 180)  →  PSK / ActorX",
                 font=(MONO, 8), bg=BG, fg=TEXT3).pack(anchor="w")

        # ── File selection panel ──────────────────────────────────────────────
        panel = tk.Frame(self, bg=BG2, padx=18, pady=16,
                         highlightbackground=BORDER, highlightthickness=1)
        panel.pack(fill="x", padx=14, pady=8)

        self._row_upk = self._file_row(
            panel, "UPK File", self._upk_path,
            "Select .upk package", self._browse_upk, row=0)

        self._row_out = self._file_row(
            panel, "Output Dir", self._out_dir,
            "Select output folder", self._browse_out, row=1)

        # ── Action bar ────────────────────────────────────────────────────────
        action = tk.Frame(self, bg=BG, padx=14, pady=6)
        action.pack(fill="x")

        self._btn_convert = tk.Button(
            action, text="  CONVERT  →  PSK",
            font=(MONO, 10, "bold"),
            bg=ACCENT, fg="#fff", activebackground=ACCENT2,
            activeforeground="#fff", relief="flat",
            cursor="hand2", padx=20, pady=8,
            command=self._on_convert)
        self._btn_convert.pack(side="left")

        self._btn_clear = tk.Button(
            action, text="CLEAR LOG",
            font=(MONO, 8), bg=BG3, fg=TEXT2,
            activebackground=BORDER, activeforeground=TEXT,
            relief="flat", cursor="hand2", padx=12, pady=8,
            command=self._clear_log)
        self._btn_clear.pack(side="left", padx=(8,0))

        self._status_lbl = tk.Label(action, text="", font=(MONO, 8),
                                     bg=BG, fg=TEXT2)
        self._status_lbl.pack(side="right", padx=4)

        # ── Progress bar (hidden initially) ──────────────────────────────────
        self._progress_frame = tk.Frame(self, bg=BG, padx=14)
        self._progress_frame.pack(fill="x")
        self._progress_bar = tk.Canvas(self._progress_frame, bg=BG3,
                                        height=3, highlightthickness=0)
        self._progress_bar.pack(fill="x")
        self._progress_fill = None

        # ── Log console ──────────────────────────────────────────────────────
        log_wrap = tk.Frame(self, bg=BG, padx=14, pady=6)
        log_wrap.pack(fill="both", expand=True)

        log_header = tk.Frame(log_wrap, bg=BG2, padx=10, pady=5,
                              highlightbackground=BORDER, highlightthickness=1)
        log_header.pack(fill="x")
        tk.Label(log_header, text="▸  CONVERSION LOG",
                 font=(MONO, 8, "bold"), bg=BG2, fg=TEXT3).pack(side="left")
        self._log_count = tk.Label(log_header, text="", font=(MONO, 8),
                                    bg=BG2, fg=TEXT3)
        self._log_count.pack(side="right")

        txt_frame = tk.Frame(log_wrap, bg=BORDER, padx=1, pady=1)
        txt_frame.pack(fill="both", expand=True)

        self._log = tk.Text(
            txt_frame, bg=BG, fg=TEXT2,
            font=(MONO, 9), relief="flat",
            insertbackground=ACCENT,
            selectbackground=ACCENT, selectforeground="#fff",
            state="disabled", wrap="none",
            padx=10, pady=8)
        self._log.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(txt_frame, orient="vertical",
                          command=self._log.yview, bg=BG2,
                          troughcolor=BG, activebackground=DIM)
        sb.pack(side="right", fill="y")
        self._log.config(yscrollcommand=sb.set)

        # Text tags for colors
        self._log.tag_config("head",   foreground=TEXT,   font=(MONO, 9, "bold"))
        self._log.tag_config("ok",     foreground=GREEN)
        self._log.tag_config("err",    foreground=RED)
        self._log.tag_config("accent", foreground=ACCENT)
        self._log.tag_config("dim",    foreground=TEXT3)

        # ── Footer ───────────────────────────────────────────────────────────
        footer = tk.Frame(self, bg=BG, padx=14, pady=4)
        footer.pack(fill="x")
        tk.Frame(footer, bg=BORDER, height=1).pack(fill="x", pady=2)
        tk.Label(footer, text="Reverse-engineered from UE3 prototype build  ·  engine ver 180",
                 font=(MONO, 7), bg=BG, fg=TEXT3).pack(side="left")

        self._log_line(
            "Drag & drop a .upk file or click the field above to browse.\n"
            "Output PSK files will be placed in the selected output directory.\n",
            "dim")

    def _file_row(self, parent, label, var, placeholder, cmd, row):
        lbl = tk.Label(parent, text=label, font=(MONO, 8, "bold"),
                       bg=BG2, fg=TEXT3, width=10, anchor="w")
        lbl.grid(row=row, column=0, sticky="w", padx=(0,10), pady=4)

        entry_frame = tk.Frame(parent, bg=BORDER, padx=1, pady=1)
        entry_frame.grid(row=row, column=1, sticky="ew", pady=4)

        entry = tk.Entry(entry_frame, textvariable=var, font=(MONO, 9),
                         bg=BG3, fg=TEXT, insertbackground=ACCENT,
                         relief="flat", bd=0)
        entry.pack(fill="x", ipady=5, padx=6)
        entry.insert(0, placeholder)
        entry.config(fg=TEXT3)

        def on_focus_in(e):
            if entry.get() == placeholder:
                entry.delete(0, "end")
                entry.config(fg=TEXT)
        def on_focus_out(e):
            if not entry.get():
                entry.insert(0, placeholder)
                entry.config(fg=TEXT3)
        entry.bind("<FocusIn>",  on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)

        btn = tk.Button(parent, text="BROWSE", font=(MONO, 8, "bold"),
                        bg=BG3, fg=DIM, activebackground=BORDER,
                        activeforeground=TEXT, relief="flat",
                        cursor="hand2", padx=10, pady=4, command=cmd)
        btn.grid(row=row, column=2, padx=(8,0), pady=4)

        parent.columnconfigure(1, weight=1)
        return entry

    # ── Browsing ──────────────────────────────────────────────────────────────

    def _browse_upk(self):
        path = filedialog.askopenfilename(
            title="Select UPK File",
            filetypes=[("Unreal Package", "*.upk"), ("All Files", "*.*")])
        if path:
            self._upk_path.set(path)
            self._row_upk.config(fg=TEXT)
            # Auto-set output dir to same folder
            if not self._out_dir.get() or self._out_dir.get() in ("Select output folder", ""):
                self._out_dir.set(os.path.dirname(path))
                self._row_out.config(fg=TEXT)

    def _browse_out(self):
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self._out_dir.set(path)
            self._row_out.config(fg=TEXT)

    # ── Conversion ───────────────────────────────────────────────────────────

    def _on_convert(self):
        if self._busy:
            return

        upk  = self._upk_path.get().strip()
        odir = self._out_dir.get().strip()

        if not upk or upk == "Select .upk package":
            messagebox.showerror("No file", "Please select a .upk file first.")
            return
        if not os.path.isfile(upk):
            messagebox.showerror("File not found", f"Cannot find:\n{upk}")
            return
        if not odir or odir == "Select output folder":
            odir = os.path.dirname(upk)
            self._out_dir.set(odir)

        os.makedirs(odir, exist_ok=True)

        self._busy = True
        self._btn_convert.config(state="disabled", bg=DIM, text="  CONVERTING…")
        self._clear_log()
        self._start_time = time.time()

        basename = os.path.basename(upk)
        self._log_line(f"{'━'*56}\n", "dim")
        self._log_line(f"  PROCESSING: {basename}\n", "head")
        self._log_line(f"  Source:  {upk}\n", "dim")
        self._log_line(f"  Output:  {odir}\n", "dim")
        self._log_line(f"{'━'*56}\n", "dim")

        self._animate_progress()

        def _log(msg):
            self.after(0, self._log_line, msg + "\n", self._tag_for(msg))

        def _done(ok, results):
            self.after(0, self._on_done, ok, results)

        t = threading.Thread(target=convert_upk,
                             args=(upk, odir, _log, _done), daemon=True)
        t.start()

    def _tag_for(self, msg):
        if "✓" in msg or "SUCCESS" in msg:  return "ok"
        if "✗" in msg or "ERROR" in msg:   return "err"
        if "►" in msg or "PROCESSING" in msg: return "accent"
        return ""

    def _on_done(self, ok, results):
        self._busy = False
        elapsed = time.time() - self._start_time

        self._log_line(f"\n{'━'*56}\n", "dim")
        if ok and results:
            self._log_line(
                f"  ✓ Done — {len(results)} mesh(es) exported in {elapsed:.2f}s\n", "ok")
            for r in results:
                self._log_line(
                    f"    {r['name']}.psk   ({r['bones']} bones, {r['faces']} faces)\n", "ok")
            self._status_lbl.config(text=f"✓  {len(results)} exported", fg=GREEN)
        elif ok and not results:
            self._log_line("  [!] No SkeletalMesh found in package.\n", "err")
            self._status_lbl.config(text="No SkeletalMesh found", fg=RED)
        else:
            self._log_line("  ✗ Conversion failed — see log above.\n", "err")
            self._status_lbl.config(text="✗  Failed", fg=RED)

        self._btn_convert.config(state="normal", bg=ACCENT, text="  CONVERT  →  PSK")
        self._stop_progress(ok and bool(results))

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _log_line(self, msg, tag=""):
        self._log.config(state="normal")
        if tag:
            self._log.insert("end", msg, tag)
        else:
            self._log.insert("end", msg)
        self._log.see("end")
        self._log.config(state="disabled")
        # Update line count
        lines = int(self._log.index("end-1c").split(".")[0])
        self._log_count.config(text=f"{lines} lines")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")
        self._status_lbl.config(text="")
        self._log_count.config(text="")

    # ── Progress bar animation ────────────────────────────────────────────────

    def _animate_progress(self):
        self._anim_frame = 0
        self._anim_running = True
        self._do_animate()

    def _do_animate(self):
        if not self._anim_running:
            return
        w = self._progress_bar.winfo_width()
        h = 3
        self._progress_bar.delete("all")
        # Travelling highlight block
        block_w = w // 4
        x = (self._anim_frame * 6) % (w + block_w) - block_w
        self._progress_bar.create_rectangle(x, 0, x+block_w, h, fill=ACCENT, outline="")
        self._anim_frame += 1
        self.after(30, self._do_animate)

    def _stop_progress(self, success):
        self._anim_running = False
        w = self._progress_bar.winfo_width()
        self._progress_bar.delete("all")
        color = GREEN if success else RED
        self._progress_bar.create_rectangle(0, 0, w, 3, fill=color, outline="")


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = App()
    app.mainloop()
