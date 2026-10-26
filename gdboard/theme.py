"""Temas de cores. Arquivo .theme em formato INI (seção [Colors], cores #RRGGBBAA)."""
from __future__ import annotations

import os
import re

# chave -> (rótulo em português, pode ser "NA"?)
FIELDS = [
    ("Background", "Fundo", False),
    ("BoardFill", "Preenchimento da placa", True),
    ("BoardOutline", "Contorno da placa", False),
    ("ComponentBox", "Contorno dos componentes", False),
    ("PinsPads", "Pinos / pads", False),
    ("GndColor", "Pinos GND", True),
    ("NcColor", "Pinos sem conexão (NC)", True),
    ("FirstPinColor", "Pino 1", True),
    ("TestPinColor", "Test points", True),
    ("ComponentLabelText", "Nome dos componentes", False),
    ("PinLabelText", "Número dos pinos", False),
    ("NetNameText", "Nome das redes (nos pinos)", False),
    ("NetHighlight", "Rede selecionada", False),
    ("NetwebCurrent", "Linhas da rede selecionada", False),
    ("NetwebOther", "Linhas (outras)", False),
    ("HighlightedPart", "Componentes na rede selecionada", False),
    ("SelectedPart", "Componente selecionado", False),
    ("SelectedPin", "Pino selecionado", False),
    ("HoverPart", "Componente sob o mouse", True),
    ("LoosePin", "Pinos soltos / componentes sem rede", True),
    ("CapacitorColor", "Capacitores", True),
    ("ResistorColor", "Resistores", True),
    ("InductorColor", "Indutores", True),
    ("JumperColor", "Jumpers", True),
    ("ICColor", "CIs / transistores", True),
    ("FuseColor", "Fusíveis", True),
]
FIELD_KEYS = [f[0] for f in FIELDS]


def parse_color(s: str):
    """'#RRGGBB' ou '#RRGGBBAA' -> (r,g,b,a) 0..255; 'NA' -> None."""
    s = (s or "").strip()
    if not s or s.upper() == "NA":
        return None
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", s)
    if not m:
        return None
    v = int(m.group(1), 16)
    a = int(m.group(2), 16) if m.group(2) else 255
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255, a)


def color_str(c):
    if c is None:
        return "NA"
    r, g, b, a = c
    return f"#{r:02X}{g:02X}{b:02X}{a:02X}"


class Theme:
    def __init__(self, name="Sem nome", colors=None, colorize=True):
        self.name = name
        self.colorize = colorize
        self.colors: dict[str, tuple | None] = {}
        base = BUILTIN["GDTech Escuro"] if BUILTIN else None
        for k in FIELD_KEYS:
            self.colors[k] = base.colors.get(k) if base else None
        if colors:
            self.colors.update(colors)

    # -------------------------------------------------- acesso com fallback
    def get(self, key, fallback=None):
        c = self.colors.get(key)
        if c is None and fallback is not None:
            c = self.colors.get(fallback)
        return c

    def qcolor(self, key, fallback=None):
        from PySide6.QtGui import QColor
        c = self.get(key, fallback)
        if c is None:
            return None
        return QColor(c[0], c[1], c[2], c[3])

    def copy(self, name=None):
        t = Theme.__new__(Theme)
        t.name = name or self.name
        t.colorize = self.colorize
        t.colors = dict(self.colors)
        return t

    # -------------------------------------------------- arquivo
    def to_ini(self) -> str:
        lines = ["[Colors]", f"ColorizeComponents={'True' if self.colorize else 'False'}"]
        for k in FIELD_KEYS:
            c = self.colors.get(k)
            if k == "Background" and c is not None:
                lines.append(f"{k}=#{c[0]:02X}{c[1]:02X}{c[2]:02X}")
            else:
                lines.append(f"{k}={color_str(c)}")
        return "\r\n".join(lines) + "\r\n"

    def save(self, path):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(self.to_ini())

    @staticmethod
    def load(path) -> "Theme":
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        t = Theme(os.path.splitext(os.path.basename(path))[0])
        for line in text.splitlines():
            if "=" not in line or line.strip().startswith(("[", ";", "#")):
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "ColorizeComponents":
                t.colorize = v.lower() in ("true", "1", "yes")
            elif k in t.colors or k in FIELD_KEYS:
                t.colors[k] = parse_color(v)
        # campos que faltam no arquivo: deriva de outros
        if t.colors.get("NetNameText") is None:
            c = t.colors.get("PinLabelText") or (200, 200, 200, 255)
            t.colors["NetNameText"] = (c[0], c[1], c[2], max(80, int(c[3] * 0.75)))
        if t.colors.get("SelectedPin") is None:
            t.colors["SelectedPin"] = (255, 255, 255, 255)
        if "HoverPart" not in text:
            t.colors["HoverPart"] = (255, 255, 255, 150)
        if "LoosePin" not in text:
            t.colors["LoosePin"] = (225, 95, 225, 255)
        for req in ("Background", "BoardOutline", "ComponentBox", "PinsPads", "ComponentLabelText",
                    "PinLabelText", "NetHighlight", "NetwebCurrent", "NetwebOther", "HighlightedPart",
                    "SelectedPart"):
            if t.colors.get(req) is None:
                t.colors[req] = BUILTIN["GDTech Escuro"].colors[req]
        return t


def _mk(name, colorize, **kw):
    t = Theme.__new__(Theme)
    t.name = name
    t.colorize = colorize
    t.colors = {k: None for k in FIELD_KEYS}
    t.colors["HoverPart"] = (255, 255, 255, 150)
    t.colors["LoosePin"] = (225, 95, 225, 255)
    for k, v in kw.items():
        t.colors[k] = parse_color(v)
    return t


BUILTIN: dict[str, Theme] = {}
BUILTIN["GDTech Escuro"] = _mk(
    "GDTech Escuro", True,
    Background="#0E1014", BoardFill="#14171CFF", BoardOutline="#E3B341FF", ComponentBox="#4AA3FFB0",
    PinsPads="#D64545FF", GndColor="#59606EFF", NcColor="#3A6B46FF", FirstPinColor="#FF7A45FF",
    TestPinColor="#2EC27EFF", ComponentLabelText="#E8E8E8E6", PinLabelText="#F2F2F2E6",
    NetNameText="#9FC9FFCC", NetHighlight="#FFE45CFF", NetwebCurrent="#FFE45CB0", NetwebOther="#3399FF90",
    HighlightedPart="#7CFC9AFF", SelectedPart="#FF9D1CFF", SelectedPin="#FFFFFFFF",
    CapacitorColor="#3A2E26FF", ResistorColor="#1A1A1EFF", InductorColor="#262A33FF",
    JumperColor="#22252BFF", ICColor="#121417FF", FuseColor="#C8C8C8FF")
BUILTIN["Verde Escuro"] = _mk(
    "Verde Escuro", True,
    Background="#082A1F", BoardFill="#0A2E22FF", BoardOutline="#B9B9B9FF", ComponentBox="#A0A0A8C0",
    PinsPads="#7C7C84FF", GndColor="#0E5C3AFF", NcColor="#4A4A52FF", FirstPinColor="NA", TestPinColor="#B08A3AFF", ComponentLabelText="#E0A060FF", PinLabelText="#FFFFFFFF",
    NetNameText="#EDEDEDE6", NetHighlight="#FFE45CFF", NetwebCurrent="#FFE45CB0", NetwebOther="#3399FF90",
    HighlightedPart="#7CFC9AFF", SelectedPart="#FF2020FF", SelectedPin="#FFFFFFFF", HoverPart="#FFFFFF90",
    CapacitorColor="#5E4446FF", ResistorColor="#161616FF", InductorColor="#3A3A46FF",
    JumperColor="#2A2A30FF", ICColor="#1B1B20FF", FuseColor="#C8C8C8FF")
# azul-claro #5BCEFA, rosa #F5A9B8 e branco
BUILTIN["Algodão-doce"] = _mk(
    "Algodão-doce", True,
    Background="#0F1724", BoardFill="#162133FF", BoardOutline="#F5A9B8FF", ComponentBox="#5BCEFA80",
    PinsPads="#5BCEFAFF", GndColor="#F5A9B8FF", NcColor="#9AA7B5FF", FirstPinColor="NA",
    TestPinColor="#E8EEF5FF", ComponentLabelText="#FFFFFFFF", PinLabelText="#14202EFF",
    NetNameText="#1E2C3ED9", NetHighlight="#FFFFFFFF", NetwebCurrent="#FFFFFFB0", NetwebOther="#5BCEFA90",
    HighlightedPart="#5BCEFAFF", SelectedPart="#F5A9B8FF", SelectedPin="#FFFFFFFF", HoverPart="#FFFFFFA0",
    CapacitorColor="#7D4A5AFF", ResistorColor="#1F4A63FF", InductorColor="#5E5E70FF",
    JumperColor="#2A3346FF", ICColor="#1A2233FF", FuseColor="#E8E8F0FF")
BUILTIN["PCB Verde"] = _mk(
    "PCB Verde", True,
    Background="#06120A", BoardFill="#0B3D1EFF", BoardOutline="#C9D46BFF", ComponentBox="#E6E6E6A0",
    PinsPads="#D9B44AFF", GndColor="#7A8F6BFF", NcColor="#4E5A48FF", FirstPinColor="#FF6B4AFF",
    TestPinColor="#E8D9A0FF", ComponentLabelText="#FFFFFFE0", PinLabelText="#101010FF",
    NetNameText="#FFFFFFB0", NetHighlight="#00E5FFFF", NetwebCurrent="#00E5FFB0", NetwebOther="#FF66CC90",
    HighlightedPart="#FFFFFFFF", SelectedPart="#FF3B3BFF", SelectedPin="#FFFFFFFF",
    CapacitorColor="#6B4B38FF", ResistorColor="#111111FF", InductorColor="#3A3A3AFF",
    JumperColor="#2D2C30FF", ICColor="#0D0D0DFF", FuseColor="#DDDDDDFF")
BUILTIN["Preto Minimalista"] = _mk(
    "Preto Minimalista", False,
    Background="#000000", BoardFill="NA", BoardOutline="#2F6B2BFF", ComponentBox="#6E6E6EFF",
    PinsPads="#5A5A5FFF", GndColor="#2F4F43FF", NcColor="#303034FF", FirstPinColor="NA",
    TestPinColor="#55565BFF", ComponentLabelText="#F8F9FAA0", PinLabelText="#F8F9FA95",
    NetNameText="#9AB8E8A0", NetHighlight="#FFFF00CC", NetwebCurrent="#FFFF00CC", NetwebOther="#3399FFD9",
    HighlightedPart="#90EE90FF", SelectedPart="#FF2B2BFF", SelectedPin="#FFFFFFFF")
BUILTIN["Claro"] = _mk(
    "Claro", True,
    Background="#F2F2EE", BoardFill="#FFFFFFFF", BoardOutline="#3B3B3BFF", ComponentBox="#5C6B7AFF",
    PinsPads="#C0392BFF", GndColor="#9AA0A6FF", NcColor="#C8CCD0FF", FirstPinColor="#E67E22FF",
    TestPinColor="#27AE60FF", ComponentLabelText="#1B1F24FF", PinLabelText="#FFFFFFFF",
    NetNameText="#1F4E8CFF", NetHighlight="#1E88E5FF", NetwebCurrent="#1E88E5B0", NetwebOther="#8E44AD90",
    HighlightedPart="#16A085FF", SelectedPart="#E53935FF", SelectedPin="#000000FF",
    CapacitorColor="#E8D9C8FF", ResistorColor="#D5D8DCFF", InductorColor="#D6DDE6FF",
    JumperColor="#E0E0E0FF", ICColor="#CFD3D8FF", FuseColor="#F5F5F5FF")


# ------------------------------------------------------------------ classificação de componentes
_TYPE_RULES = [
    ("FuseColor", re.compile(r"^(P?F|P?FB|PFUSE|FUSE)\d", re.I)),
    ("CapacitorColor", re.compile(r"^(P?C|EC|PCE|CE|PMCE|PGCE|TC)\d", re.I)),
    ("ResistorColor", re.compile(r"^(P?R|RN|PRN|RA|PR)\d", re.I)),
    ("InductorColor", re.compile(r"^(P?L|CHOKE)\d", re.I)),
    ("JumperColor", re.compile(r"^(P?J|JP|PJP|JS)\d", re.I)),
    ("ICColor", re.compile(r"^(P?U|P?Q|IC|PD|D|Y|X|PZ)\d", re.I)),
]


def component_type(name: str):
    for key, rx in _TYPE_RULES:
        if rx.match(name):
            return key
    return None
