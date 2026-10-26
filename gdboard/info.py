"""Textos de informação de componentes/pinos (caixa ao passar o mouse e painel lateral)."""
from __future__ import annotations

import html
import re

from .parsers import TOP, BOTTOM, BOTH

MIL_MM = 0.0254

# tamanho nominal (mm) dos encapsulamentos em código imperial
CHIP_SIZES = {
    "01005": (0.4, 0.2), "0201": (0.6, 0.3), "0402": (1.0, 0.5), "0603": (1.6, 0.8), "0805": (2.0, 1.25),
    "1206": (3.2, 1.6), "1210": (3.2, 2.5), "1812": (4.5, 3.2), "2010": (5.0, 2.5), "2512": (6.4, 3.2),
}
_RX_CHIP = re.compile(r"(?<![0-9])(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])")
_RX_PKG = re.compile(r"(?<![A-Za-z])((?:[TLHV]?SSOP|TSOP|SOIC|SOP|TSOT|SOT|SOD|PDFN|WSON|[UVWX]?[QD]FN|"
                     r"[LTVP]?QFP|[FPLW]?BGA|D2PAK|DPAK|TO|SON|LGA|CSP|MLF)[-_]?\d+[A-Z0-9\-]*)", re.I)
_RX_VAL = [
    re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?\s*[KMR]?\s*OHMS?)(?![A-Za-z])", re.I),
    re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?\s*[KMR]?\s*Ω)", re.I),
    re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?\s*[PNUµM]F)(?![A-Za-z])", re.I),
    re.compile(r"(?<![\w.])(\d+(?:[.,]\d+)?\s*[PNUµM]H)(?![A-Za-z])", re.I),
]
_TYPE_NAMES = {"CapacitorColor": "Capacitor", "ResistorColor": "Resistor", "InductorColor": "Indutor",
               "JumperColor": "Jumper / conector", "ICColor": "CI / semicondutor", "FuseColor": "Fusível"}


def side_name(side):
    return {TOP: "Topo (Top)", BOTTOM: "Baixo (Bottom)", BOTH: "Ambos (furo passante)"}.get(side, "?")


def package_of(part):
    for txt in (part.footprint, part.mfg):
        if not txt:
            continue
        m = _RX_CHIP.search(txt)
        if m:
            return m.group(1)
    for txt in (part.footprint, part.mfg):
        if txt:
            m = _RX_PKG.search(txt)
            if m:
                return m.group(1).upper()
    return ""


def value_of(part, ptype):
    mfg = part.mfg or ""
    if not mfg or ptype not in ("ResistorColor", "CapacitorColor", "InductorColor", "FuseColor"):
        return ""
    for rx in _RX_VAL[:4]:
        m = rx.search(mfg)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    # formato "12.7k/4/1/x 0402" (TVW/FZ): primeiro campo antes da barra
    if ptype in ("ResistorColor", "CapacitorColor", "InductorColor", "FuseColor"):
        tok = re.split(r"[/\s]", mfg.strip())[0]
        if re.match(r"^\d+(?:[.,]\d+)?[a-zA-Zµ]*$", tok) and not _RX_CHIP.fullmatch(tok):
            return tok
    return ""


def fmt_mm(mils):
    mm = mils * MIL_MM
    return f"{mm:.2f}" if mm < 10 else f"{mm:.1f}"


def size_text(board, part):
    """Tamanho do componente: nominal pelo encapsulamento quando conhecido; senão a área ocupada pelos pads."""
    pkg = package_of(part)
    if pkg in CHIP_SIZES:
        a, b = CHIP_SIZES[pkg]
        return f"{a:g} × {b:g} mm (padrão {pkg})"
    L, W = part.dims
    if L <= 0:
        return ""
    return f"≈ {fmt_mm(L)} × {fmt_mm(W)} mm"


def part_card_html(board, pi, hover_pin=None, ptype=None, max_pins=18, colors=None, note=None):
    b = board
    part = b.parts[pi]
    e = html.escape
    c = colors or {}
    accent = c.get("accent", "#5B8DEF")
    dim = c.get("dim", "#9AA0A6")
    gnd = c.get("gnd", "#3FB97A")
    hl = c.get("hl", "#FFD54A")
    rows = [f"<div style='font-size:13px'><span style='color:{accent}'>&#9432;</span>&nbsp;<b>{e(part.name)}</b></div>"]
    if note:
        tag, text, color = note
        label = e(tag) + (f": {e(text)}" if text else "")
        rows.append(f"<div style='margin:2px 0 4px 0'><span style='background:{color}; color:#111;"
                    f" padding:0 4px; border-radius:3px'>&#9873; {label}</span></div>")
    info = []
    if part.mfg:
        info.append(("Info", e(part.mfg)))
    tname = _TYPE_NAMES.get(ptype or "")
    val = value_of(part, ptype)
    if val:
        info.append(("Valor", e(val)))
    if tname:
        info.append(("Tipo", tname))
    pkg = package_of(part)
    if part.footprint:
        info.append(("Footprint", e(part.footprint)))
    elif pkg:
        info.append(("Encapsulamento", e(pkg)))
    sz = size_text(b, part)
    if sz:
        info.append(("Tamanho", e(sz)))
    npins = len(part.pins)
    pin_line = f"{npins}"
    if npins >= 2 and part.pitch > 0:
        pin_line += f" &nbsp;<span style='color:{dim}'>(passo {fmt_mm(part.pitch)} mm)</span>"
    info.append(("Pinos", pin_line))
    info.append(("Lado", side_name(part.side)))
    for k, v in info:
        rows.append(f"<div><b>{k}:</b> {v}</div>")
    pins = part.pins
    if pins:
        rows.append(f"<hr style='border:0; border-top:1px solid #555; margin:3px 0'>")
        show = list(pins[:max_pins])
        if hover_pin is not None and hover_pin in pins and hover_pin not in show:
            show = show[:max_pins - 1] + [hover_pin]
        lines = []
        for i in show:
            net = b.nets[b.net[i]]
            num = b.num[i]
            nm = b.pname[i]
            label = e(num) + (f" <span style='color:{dim}'>({e(nm)})</span>" if nm and nm != num else "")
            netc = gnd if b.net_is_gnd[b.net[i]] else (dim if b.net_is_nc[b.net[i]] else "")
            nets = f"<span style='color:{netc}'>{e(net)}</span>" if netc else e(net)
            line = f"{label}: {nets}"
            if i == hover_pin:
                line = f"<span style='color:{hl}'>&#9656;</span> <b>{line}</b>"
            else:
                line = "&nbsp;&nbsp;&nbsp;" + line
            lines.append(f"<div>{line}</div>")
        rows += lines
        if len(pins) > len(show):
            rows.append(f"<div style='color:{dim}'>&nbsp;&nbsp;&nbsp;… mais {len(pins) - len(show)} pinos</div>")
    return "".join(rows)


def pin_card_html(board, i, colors=None):
    """Pino sem componente (ponto de teste / nail)."""
    b = board
    e = html.escape
    c = colors or {}
    accent = c.get("accent", "#5B8DEF")
    net = b.nets[b.net[i]]
    rows = [f"<div style='font-size:13px'><span style='color:{accent}'>&#9432;</span>&nbsp;<b>{e(b.pin_label(i))}</b></div>",
            f"<div><b>Rede:</b> {e(net)}</div>",
            f"<div><b>Lado:</b> {side_name(int(b.side[i]))}</div>",
            f"<div><b>Diâmetro:</b> ≈ {fmt_mm(2 * float(b.r[i]))} mm</div>"]
    if b.probe[i] > 0:
        rows.append(f"<div><b>Probe:</b> {int(b.probe[i])}</div>")
    return "".join(rows)
