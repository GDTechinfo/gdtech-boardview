"""
Leitores de arquivos de boardview.

Portados (em Python) a partir dos leitores do OpenBoardView (licença MIT),
mantendo o mesmo comportamento para cada formato:

  BRD (Test_Link / codificado)   .brd .bv
  BRD2 (BRDOUT:)                 .brd .bv
  BDV (Honhan, codificado)       .bdv
  ASC (format/pins/nails.asc)    .asc
  BVR (BVRAW_FORMAT_1)           .bvr
  BVR3 (BVRAW_FORMAT_3)          .bvr
  CAD (###Panel Added / C_PIN)   .cad
  CST (binário, IBM/Lenovo)      .cst
  FZ / CAE (RC6 + zlib)          .fz .cae   (precisa da chave, exceto arquivos sem criptografia)
  XZZPCB (DES)                   .pcb       (precisa da chave)

A detecção é feita pelo CONTEÚDO, então arquivos com extensão "estranha"
(.bv, .gr, .f2b, .faz...) abrem normalmente quando forem uma dessas variantes.
"""
from __future__ import annotations

import math
import os
import re
import struct
import zlib

import numpy as np

# --------------------------------------------------------------------------- tipos

BOTH, TOP, BOTTOM = 0, 1, 2


class RPart:
    __slots__ = ("name", "side", "smd", "end_of_pins", "p1", "p2", "mfg", "footprint")

    def __init__(self, name="", side=BOTH, smd=True):
        self.footprint = ""
        self.name = name
        self.side = side
        self.smd = smd
        self.end_of_pins = 0
        self.p1 = (0, 0)
        self.p2 = (0, 0)
        self.mfg = ""


class RPin:
    __slots__ = ("x", "y", "probe", "part", "side", "net", "radius", "snum", "name", "pad")

    def __init__(self):
        self.x = 0
        self.y = 0
        self.probe = 0
        self.part = 0          # índice 1-based do componente (como no OBV)
        self.side = BOTH
        self.net = "UNCONNECTED"
        self.radius = 0.5      # 0.5 = "não informado pelo arquivo"
        self.snum = None
        self.name = None
        self.pad = None        # pad real do arquivo: (cx, cy, meia-larg, meia-alt, eixo.x, eixo.y, arredondamento 0..1)


class RNail:
    __slots__ = ("probe", "x", "y", "side", "net")

    def __init__(self):
        self.probe = 0
        self.x = 0
        self.y = 0
        self.side = BOTH
        self.net = "UNCONNECTED"


class RawLayer:
    """Camada de cobre (trilhas, áreas de cobre e pads/vias). Redes são índices em RawBoard.layer_nets.
    segs: lista de arrays (n, 6) = x1, y1, x2, y2, largura, rede
    polys: lista de (rede, contorno (m, 2), [furos (c, 2)])
    pads: lista de arrays (k, 6) = x, y, meia-larg, meia-alt, arredondamento, rede"""

    def __init__(self, name):
        self.name = name
        self.segs = []
        self.polys = []
        self.pads = []

    def add_arc(self, cx, cy, r, a0, sweep, w, net):
        """Arco (graus) quebrado em segmentos."""
        if r <= 0 or sweep == 0:
            return
        n = max(2, min(64, int(abs(sweep) / 10) + 2))
        t = np.radians(a0 + np.linspace(0.0, sweep, n))
        x = cx + r * np.cos(t)
        y = cy + r * np.sin(t)
        seg = np.empty((n - 1, 6))
        seg[:, 0], seg[:, 1], seg[:, 2], seg[:, 3] = x[:-1], y[:-1], x[1:], y[1:]
        seg[:, 4], seg[:, 5] = w, net
        self.segs.append(seg)

    def add_arcs(self, cx, cy, r, a0, sweep, w, net):
        """Vários arcos de uma vez (vetorizado); ângulos em graus."""
        cx, cy, r, a0, sweep, w, net = (np.asarray(v, dtype=np.float64) for v in (cx, cy, r, a0, sweep, w, net))
        ok = (r > 0) & (sweep != 0)
        if not ok.any():
            return
        cx, cy, r, a0, sweep, w, net = (v[ok] for v in (cx, cy, r, a0, sweep, w, net))
        nseg = np.clip((np.abs(sweep) / 10).astype(np.int64) + 1, 1, 63)
        for k in np.unique(nseg):
            m = nseg == k
            f = np.linspace(0.0, 1.0, k + 1)
            t = np.radians(a0[m, None] + sweep[m, None] * f[None, :])
            x = cx[m, None] + r[m, None] * np.cos(t)
            y = cy[m, None] + r[m, None] * np.sin(t)
            seg = np.empty((m.sum(), k, 6))
            seg[:, :, 0], seg[:, :, 1] = x[:, :-1], y[:, :-1]
            seg[:, :, 2], seg[:, :, 3] = x[:, 1:], y[:, 1:]
            seg[:, :, 4] = w[m, None]
            seg[:, :, 5] = net[m, None]
            self.segs.append(seg.reshape(-1, 6))

    def empty(self):
        return not (self.segs or self.polys or self.pads)


class RawBoard:
    def __init__(self, fmt_name: str):
        self.layers: list[RawLayer] = []
        self.layer_nets: list[str] = []
        self.format_name = fmt_name
        self.format: list[tuple[float, float]] = []
        self.outline_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
        self.parts: list[RPart] = []
        self.pins: list[RPin] = []
        self.nails: list[RNail] = []
        self.valid = False

    # igual a BRDFileBase::AddNailsAsPins
    def add_nails_as_pins(self):
        for nail in self.nails:
            pin = RPin()
            pin.x, pin.y = nail.x, nail.y
            if nail.side == BOTH:
                pin.part = len(self.parts)
                pin.side = BOTH
            elif nail.side == TOP:
                pin.part = len(self.parts)
                pin.side = TOP
            else:
                pin.part = len(self.parts) - 1
                pin.side = BOTTOM
            pin.probe = nail.probe
            pin.net = nail.net
            self.pins.append(pin)

    def gen_outline(self, margin=20):
        if not self.pins:
            return
        xs = [p.x for p in self.pins]
        ys = [p.y for p in self.pins]
        minx, maxx = min(xs) - margin, max(xs) + margin
        miny, maxy = min(ys) - margin, max(ys) + margin
        self.format = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy), (minx, miny)]


class FormatError(Exception):
    pass


class KeyRequired(FormatError):
    """O arquivo é criptografado e precisa de uma chave nas Configurações."""


# --------------------------------------------------------------------------- utilidades

_WS = " \t\n\r\f\v"
_RE_INT = re.compile(r"[ \t\n\r\f\v]*([+-]?\d+)")
_RE_DBL = re.compile(r"[ \t\n\r\f\v]*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")


class Cur:
    """Cursor que imita strtol/strtod/READ_STR do código C do OpenBoardView."""
    __slots__ = ("s", "p", "n")

    def __init__(self, s: str, p: int = 0):
        self.s = s
        self.p = p
        self.n = len(s)

    def int(self) -> int:
        m = _RE_INT.match(self.s, self.p)
        if not m:
            return 0
        self.p = m.end()
        return int(m.group(1))

    def dbl(self) -> float:
        m = _RE_DBL.match(self.s, self.p)
        if not m:
            return 0.0
        self.p = m.end()
        return float(m.group(1))

    def str(self) -> str:
        s, p, n = self.s, self.p, self.n
        while p < n and s[p] in _WS:
            p += 1
        st = p
        while p < n and s[p] not in _WS:
            p += 1
        self.p = min(p + 1, n)
        return s[st:p]

    def str2(self) -> str:
        """READ_STR2: termina só em espaço DUPLO (nomes com espaço simples)."""
        s, p, n = self.s, self.p, self.n
        while p < n and s[p] in _WS:
            p += 1
        st = p
        while p + 1 < n and (s[p] not in _WS or s[p + 1] not in _WS):
            p += 1
        if p + 1 >= n:
            p = n
        self.p = min(p + 1, n)
        return s[st:p]

    def skip(self, k: int):
        self.p = min(self.p + k, self.n)

    def until(self, ch: str) -> str:
        """Lê até o delimitador `ch` (usado pelo FZ, delimitador '!')."""
        s, p, n = self.s, self.p, self.n
        while p < n and s[p] in _WS:
            p += 1
        st = p
        e = s.find(ch, p)
        if e < 0:
            e = n
        self.p = min(e + 1, n)
        return s[st:e]


_NL = re.compile(rb"[\r\n]")


def stringfile(data: bytes) -> list[str]:
    """Port exato de stringfile() (stb.h) — importa para os 'pula N linhas'."""
    z = data.find(b"\0")
    if z >= 0:
        data = data[:z]
    n = len(data)
    starts = [0]
    ends = []
    # mesmo resultado do laço byte a byte do stb (inclusive as manias: \r\n ou \n\n contam como uma quebra,
    # e o 1º caractere de cada linha nunca é visto como quebra), mas pulando direto de quebra em quebra
    find = _NL.search
    s = 0
    while True:
        m = find(data, s)
        if m is None:
            break
        e = m.start()
        ends.append(e)
        s = e + 1
        if s < n and (data[s] == 10 or data[s] == 13):
            s += 1
        if s < n:
            starts.append(s)
        s += 1
    if len(ends) < len(starts):
        ends.append(n)
    text = data.decode("latin-1")
    return [text[a:b] for a, b in zip(starts, ends)]


def fix_text(s: str) -> str:
    """Nomes vêm como latin-1; se forem UTF-8 válido, decodifica como UTF-8."""
    if s.isascii():
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return s


def _lstrip(line: str) -> str:
    return line.lstrip(_WS)


# --------------------------------------------------------------------------- BRD

BRD_SIGNATURE = bytes((0x23, 0xE2, 0x63, 0x28))


def verify_brd(buf: bytes) -> bool:
    if buf[:4] == BRD_SIGNATURE:
        return True
    return b"str_length:" in buf and b"var_data:" in buf


def parse_brd(buf: bytes) -> RawBoard:
    b = RawBoard("BRD")
    if buf[:4] == BRD_SIGNATURE:
        table = bytearray(((~(((c >> 6) & 3) | (c << 2))) & 0xFF) for c in range(256))
        table[0], table[10], table[13] = 0, 10, 13
        buf = buf.translate(bytes(table))

    block = 0
    num_parts = num_pins = 0
    for line in stringfile(buf):
        line = _lstrip(line)
        if not line:
            continue
        if line == "str_length:":
            block = 1; continue
        if line == "var_data:":
            block = 2; continue
        if line in ("Format:", "format:"):
            block = 3; continue
        if line in ("Parts:", "Pins1:"):
            block = 4; continue
        if line in ("Pins:", "Pins2:"):
            block = 5; continue
        if line == "Nails:":
            block = 6; continue
        c = Cur(line)
        if block == 2:
            c.int(); num_parts = c.int(); num_pins = c.int(); c.int()
        elif block == 3:
            x = c.int(); y = c.int()
            b.format.append((x, y))
        elif block == 4:
            part = RPart(fix_text(c.str()))
            tmp = c.int()
            part.smd = bool(tmp & 0xC)
            if tmp == 1 or 4 <= tmp < 8:
                part.side = TOP
            elif tmp == 2 or tmp >= 8:
                part.side = BOTTOM
            part.end_of_pins = c.int()
            b.parts.append(part)
        elif block == 5:
            pin = RPin()
            pin.x = c.int(); pin.y = c.int()
            pin.probe = c.int()
            pin.part = c.int()
            pin.net = fix_text(c.str())
            b.pins.append(pin)
        elif block == 6:
            nail = RNail()
            nail.probe = c.int()
            nail.x = c.int(); nail.y = c.int()
            nail.side = TOP if c.int() == 1 else BOTTOM
            nail.net = fix_text(c.str())
            b.nails.append(nail)

    # Variante Lenovo: rede do pino vem do "nail" com o mesmo probe
    nail_net = {n.probe: n.net for n in b.nails}
    np_ = len(b.parts)
    for pin in b.pins:
        if pin.net == "":
            pin.net = nail_net.get(pin.probe, "")
        if 1 <= pin.part <= np_:
            pin.side = b.parts[pin.part - 1].side
    b.valid = block != 0
    return b


# --------------------------------------------------------------------------- BRD2

def verify_brd2(buf: bytes) -> bool:
    return b"BRDOUT:" in buf and b"NETS:" in buf


def parse_brd2(buf: bytes) -> RawBoard:
    b = RawBoard("BRD2")
    nets: dict[int, str] = {}
    maxx = maxy = 0
    block = 0
    for line in stringfile(buf):
        line = _lstrip(line)
        if not line:
            continue
        if line.startswith("BRDOUT:"):
            c = Cur(line, 7); block = 1
            c.int(); maxx = c.int(); maxy = c.int(); continue
        if line.startswith("NETS:"):
            block = 2; continue
        if line.startswith("PARTS:"):
            block = 3; continue
        if line.startswith("PINS:"):
            block = 4; continue
        if line.startswith("NAILS:"):
            block = 5; continue
        c = Cur(line)
        if block == 1:
            x = c.int(); y = c.int()
            b.format.append((x, y))
        elif block == 2:
            i = c.int()
            nets[i] = fix_text(c.str())
        elif block == 3:
            part = RPart(fix_text(c.str()))
            part.p1 = (c.int(), c.int())
            part.p2 = (c.int(), c.int())
            part.end_of_pins = c.int()   # neste formato: INÍCIO dos pinos
            side = c.int()
            part.side = TOP if side == 1 else BOTTOM if side == 2 else BOTH
            b.parts.append(part)
        elif block == 4:
            pin = RPin()
            pin.x = c.int(); pin.y = c.int()
            netid = c.int()
            side = c.int()
            pin.side = TOP if side == 1 else BOTTOM if side == 2 else BOTH
            pin.net = nets.get(netid, "")
            pin.probe = 1
            b.pins.append(pin)
        elif block == 5:
            nail = RNail()
            nail.probe = c.int()
            nail.x = c.int(); nail.y = c.int()
            nail.net = nets.get(c.int(), "UNCONNECTED")
            if c.int() == 1:
                nail.side = TOP
            else:
                nail.side = BOTTOM
                nail.y = maxy - nail.y
            b.nails.append(nail)

    cpi = 0
    parts = b.parts
    pins = b.pins
    for i, part in enumerate(parts):
        is_dip = True
        if part.side == BOTTOM:
            part.p1 = (part.p1[0], maxy - part.p1[1])
            part.p2 = (part.p2[0], maxy - part.p2[1])
        pei = len(pins) if i == len(parts) - 1 else parts[i + 1].end_of_pins
        pei = min(pei, len(pins))
        while cpi < pei:
            pin = pins[cpi]
            pin.part = i + 1
            if pin.side != TOP:
                pin.y = maxy - pin.y
            if (pin.side == TOP and part.side == TOP) or (pin.side == BOTTOM and part.side == BOTTOM):
                is_dip = False
            cpi += 1
        if is_dip:
            part.smd = False
            part.side = BOTH
        else:
            part.smd = True

    for i in (1, 2):
        d = RPart("...", BOTTOM if i == 1 else TOP)
        parts.append(d)
    b.add_nails_as_pins()
    b.valid = block != 0
    return b


# --------------------------------------------------------------------------- BDV

def verify_bdv(buf: bytes) -> bool:
    return b"dd:1.3?,r?-=bb" in buf or (b"<<format.asc>>" in buf and b"<<pins.asc>>" in buf)


def decode_bdv(buf: bytes) -> bytes:
    """Decodifica o BDV (chave incrementa a cada CRLF). Vetorizado com numpy.

    Equivalente ao laço do OBV: count começa em 0xA0, soma 1 em cada CRLF e,
    ao passar de 285, volta a 159.
    """
    import numpy as np
    a = np.frombuffer(buf, dtype=np.uint8)
    if a.size == 0:
        return buf
    crlf = np.zeros(a.size, dtype=np.int64)
    crlf[:-1] = (a[:-1] == 13) & (a[1:] == 10)
    k = np.cumsum(crlf)
    m = k % 127
    key = np.where(m == 126, 159, 160 + m)
    dec = ((key - a.astype(np.int64)) & 0xFF).astype(np.uint8)
    keep = (a == 13) | (a == 10) | (a == 0)
    return np.where(keep, a, dec).astype(np.uint8).tobytes()


def decode_bdv_slow(buf: bytes) -> bytes:
    out = bytearray(buf)
    n = len(out)
    count = 0xA0
    for i in range(n):
        x = buf[i]
        if x == 13 and i + 1 < n and buf[i + 1] == 10:
            count += 1
        if not (x == 13 or x == 10 or x == 0):
            x = (count - x) & 0xFF
        if count > 285:
            count = 159
        out[i] = x
    return bytes(out)


def _parse_asc_like(b: RawBoard, lines: list[str], fmt_skip=8, pins_skip=8, nails_skip=7,
                    use_str2=False):
    block = 0
    i = 0
    n = len(lines)
    while i < n:
        line = _lstrip(lines[i]); i += 1
        if not line:
            continue
        if line == "<<format.asc>>":
            block = 1; i += fmt_skip; continue
        if line == "<<pins.asc>>":
            block = 2; i += pins_skip; continue
        if line == "<<nails.asc>>":
            block = 3; i += nails_skip; continue
        _asc_line(b, block, line, use_str2)
    return block


def _asc_line(b: RawBoard, block: int, line: str, use_str2: bool):
    c = Cur(line)
    if block == 1:
        x = c.dbl(); y = c.dbl()
        b.format.append((x * 1000.0, y * 1000.0))
    elif block == 2:
        if line.startswith("Part"):
            c.skip(4)
            part = RPart(fix_text(c.str()))
            loc = c.str()
            part.side = TOP if loc == "(T)" else BOTTOM
            b.parts.append(part)
        else:
            if not b.parts:
                return
            pin = RPin()
            pin.part = len(b.parts)
            c.int()
            pname = c.str2() if use_str2 else c.str()
            pin.name = fix_text(pname) or None
            pin.x = c.dbl() * 1000.0
            pin.y = c.dbl() * 1000.0
            c.int()
            pin.net = fix_text(c.str())
            pin.probe = c.int()
            pin.side = b.parts[-1].side
            b.pins.append(pin)
            b.parts[-1].end_of_pins = len(b.pins)
    elif block == 3:
        nail = RNail()
        c.skip(1)
        nail.probe = c.int()
        nail.x = c.dbl() * 1000.0
        nail.y = c.dbl() * 1000.0
        c.int(); c.str()
        loc = c.str()
        nail.side = TOP if loc == "(T)" else BOTTOM
        c.str()
        nail.net = fix_text(c.str())
        b.nails.append(nail)


def parse_bdv(buf: bytes) -> RawBoard:
    b = RawBoard("BDV")
    # O OBV sempre decodifica; aqui só decodificamos se não for texto puro.
    plain = b"<<format.asc>>" in buf and b"<<pins.asc>>" in buf
    data = buf if plain else decode_bdv(buf)
    block = _parse_asc_like(b, stringfile(data))
    # No OBV, nomes de pino do BDV não são usados (número sequencial)
    for p in b.pins:
        p.name = None
    b.valid = block != 0
    return b


# --------------------------------------------------------------------------- ASC (pasta)

def _lookup_insensitive(directory: str, name: str) -> str | None:
    try:
        for f in os.listdir(directory):
            if f.lower() == name.lower():
                return os.path.join(directory, f)
    except OSError:
        pass
    return None


def parse_asc(path: str) -> RawBoard:
    b = RawBoard("ASC")
    d = os.path.dirname(os.path.abspath(path))
    files = {}
    for nm in ("format.asc", "pins.asc", "nails.asc"):
        fp = _lookup_insensitive(d, nm)
        if not fp:
            raise FormatError(f"Formato ASC: o arquivo '{nm}' precisa estar na mesma pasta.")
        files[nm] = fp

    def run(fp, block, skip):
        with open(fp, "rb") as f:
            lines = stringfile(f.read())
        first = True
        i = 0
        while i < len(lines):
            line = _lstrip(lines[i]); i += 1
            if not line:
                continue
            if first:
                first = False
                i += skip
                continue
            _asc_line(b, block, line, use_str2=True)

    run(files["format.asc"], 1, 7)
    run(files["pins.asc"], 2, 7)
    run(files["nails.asc"], 3, 6)
    b.valid = True
    return b


# --------------------------------------------------------------------------- BVR (1)

def verify_bvr(buf: bytes) -> bool:
    return b"BVRAW_FORMAT_1" in buf


def parse_bvr(buf: bytes) -> RawBoard:
    b = RawBoard("BVR")
    lines = stringfile(buf)
    block = 0
    prev_part = None
    i = 0
    while i < len(lines):
        line = _lstrip(lines[i]); i += 1
        if not line:
            continue
        if line == "<<Layout>>":
            block = 1; i += 1; continue
        if line == "<<Pin>>":
            block = 2; i += 1; continue
        if line == "<<Nail>>":
            block = 3; i += 1; continue
        c = Cur(line)
        if block == 1:
            x = c.dbl()
            if c.p < c.n and line[c.p] == ",":
                c.p += 1
            y = c.dbl()
            b.format.append((math.trunc(x * 1000), math.trunc(y * 1000)))
        elif block == 2:
            name = fix_text(c.str())
            loc = c.str()
            side = TOP if loc == "(T)" else BOTTOM
            if name != prev_part:
                b.parts.append(RPart(name, side))
                prev_part = name
            pin = RPin()
            pin.part = len(b.parts)
            c.int()
            pin.name = fix_text(c.str()) or None
            pin.x = math.trunc(c.dbl() * 1000)
            pin.y = math.trunc(c.dbl() * 1000)
            c.int()
            pin.net = fix_text(c.str())
            pin.side = side
            b.pins.append(pin)
            b.parts[-1].end_of_pins = len(b.pins)
        elif block == 3:
            nail = RNail()
            t = line.find("\t")
            c.p = t + 1 if t >= 0 else c.n
            x = c.dbl(); y = c.dbl()
            c.int(); c.str()
            loc = c.str()
            nail.side = TOP if loc == "(T)" else BOTTOM
            c.str()
            nail.net = fix_text(c.str())
            nail.x, nail.y = x * 1000, y * 1000
            b.nails.append(nail)
    b.valid = block != 0
    return b


# --------------------------------------------------------------------------- BVR3

def verify_bvr3(buf: bytes) -> bool:
    return b"BVRAW_FORMAT_3" in buf


def _side_letter(s: str) -> int:
    return TOP if s == "T" else BOTTOM if s == "B" else BOTH


def parse_bvr3(buf: bytes) -> RawBoard:
    b = RawBoard("BVR3")
    part = RPart()
    part_pos = (0, 0)
    pin = RPin()
    segs = []
    for line in stringfile(buf):
        line = _lstrip(line)
        if not line:
            continue
        if line.startswith("PART_NAME "):
            part.name = fix_text(Cur(line, 10).str())
        elif line.startswith("PART_SIDE "):
            part.side = _side_letter(Cur(line, 10).str())
        elif line.startswith("PART_ORIGIN "):
            c = Cur(line, 12)
            part_pos = (math.trunc(c.dbl()), math.trunc(c.dbl()))
        elif line.startswith("PART_MOUNT "):
            part.smd = Cur(line, 11).str() == "SMD"
        elif line.startswith("PIN_NUMBER "):
            pin.snum = fix_text(Cur(line, 11).str())
        elif line.startswith("PIN_NAME "):
            pin.name = fix_text(Cur(line, 9).str())
        elif line.startswith("PIN_SIDE "):
            pin.side = _side_letter(Cur(line, 9).str())
        elif line.startswith("PIN_ORIGIN "):
            c = Cur(line, 11)
            pin.x = math.trunc(c.dbl()) + part_pos[0]
            pin.y = math.trunc(c.dbl()) + part_pos[1]
        elif line.startswith("PIN_RADIUS "):
            pin.radius = Cur(line, 11).dbl()
        elif line.startswith("PIN_NET "):
            pin.net = fix_text(Cur(line, 8).str())
        elif line == "PIN_END":
            pin.part = len(b.parts) + 1
            b.pins.append(pin)
            pin = RPin()
        elif line == "PART_END":
            part.end_of_pins = len(b.pins)
            b.parts.append(part)
            part = RPart()
            part_pos = (0, 0)
        elif line.startswith("OUTLINE_POINTS "):
            c = Cur(line, 15)
            while c.p < c.n:
                old = c.p
                x = c.dbl(); y = c.dbl()
                if c.p == old:
                    break
                b.format.append((math.trunc(x), math.trunc(y)))
        elif line.startswith("OUTLINE_SEGMENTED "):
            c = Cur(line, 18)
            while c.p < c.n:
                old = c.p
                v = [c.dbl() for _ in range(4)]
                if c.p == old:
                    break
                segs.append(((math.trunc(v[0]), math.trunc(v[1])), (math.trunc(v[2]), math.trunc(v[3]))))
    if segs:
        # Os segmentos são desenhados direto, sem precisar encadear
        b.outline_segments.extend(segs)
    b.valid = bool(b.parts) or bool(b.format) or bool(segs)
    return b


# --------------------------------------------------------------------------- CAD

def verify_cad(buf: bytes) -> bool:
    return b"###Panel Added" in buf and b"C_PIN" in buf


def parse_cad(buf: bytes) -> RawBoard:
    b = RawBoard("CAD")
    mult = 1000.0
    parts_id: dict[str, int] = {}
    nailnet = ""
    any_block = False
    for line in stringfile(buf):
        line = _lstrip(line)
        if not line:
            continue
        c = Cur(line)
        if line.startswith("COMP"):
            any_block = True
            c.str()
            part = RPart(fix_text(c.str()))
            for _ in range(5):
                c.str()
            loc = c.str()
            part.side = TOP if loc == "1" else BOTTOM
            b.parts.append(part)
            parts_id[part.name] = len(b.parts)
        elif line.startswith("C_PIN"):
            any_block = True
            c.str()
            pname = c.str().split("-", 1)[0]
            pn = parts_id.get(fix_text(pname))
            if pn is None:
                continue
            pin = RPin()
            pin.part = pn
            pin.x = c.dbl() * mult
            pin.y = c.dbl() * mult
            c.dbl(); c.dbl(); c.dbl(); c.str()
            net = c.str()
            if "/" in net:
                net = net[1:]
            pin.net = fix_text(net)
            pin.side = b.parts[pn - 1].side
            b.pins.append(pin)
        elif line.startswith("NET "):
            any_block = True
            c.str()
            net = c.str()
            if "/" in net:
                net = net[1:]
            nailnet = fix_text(net)
        elif line.startswith("N_VIA"):
            any_block = True
            nail = RNail()
            nail.net = nailnet
            c.str()
            nail.x = c.dbl() * mult
            nail.y = c.dbl() * mult
            c.str()
            nail.side = TOP if c.dbl() == 1 else BOTTOM
            b.nails.append(nail)
    b.gen_outline()
    b.valid = any_block
    return b


# --------------------------------------------------------------------------- CST

def parse_cst(buf: bytes) -> RawBoard:
    import struct
    b = RawBoard("CST")
    mv = buf + b"\0" * 16
    p = 0

    def short():
        nonlocal p
        v = struct.unpack_from("<h", mv, p)[0]
        p += 2
        return v

    def byte():
        nonlocal p
        v = mv[p]
        p += 1
        return v - 256 if v > 127 else v

    try:
        num_parts = short()
        p += 4
        sl = short()
        p += sl
        for _ in range(num_parts):
            sl = byte()
            name = mv[p:p + sl].decode("latin-1"); p += sl
            p += 4
            layer = byte()
            part = RPart(fix_text(name))
            if layer == 0x0C:
                part.side = TOP
            elif layer == 0x01:
                part.side = BOTTOM
            b.parts.append(part)
            p += 6
        p -= 2
        num_nets = short()
        nets = []
        for _ in range(num_nets):
            sl = byte()
            nets.append(fix_text(mv[p:p + sl].decode("latin-1")))
            p += sl
        b.parts.append(RPart("...", BOTH, smd=False))
        q = mv.find(b"CPad", p)
        if q < 0:
            raise FormatError("CST: seção CPad não encontrada")
        p = q - 8
        num_pins = short()
        p += 10
        for _ in range(num_pins):
            if p + 16 > len(buf):
                break
            part_id = short()
            pin = RPin()
            pin.part = part_id + 1 if part_id >= 0 else len(b.parts)
            pin.probe = short()
            net_id = short()
            pin.net = nets[net_id] if 0 <= net_id < len(nets) else ""
            pin.x = short()
            pin.y = short()
            if 1 <= pin.part <= len(b.parts):
                pin.side = b.parts[pin.part - 1].side
            short()
            p += 4
            b.pins.append(pin)
    except (struct.error, IndexError) as e:
        raise FormatError(f"CST inválido: {e}")
    b.gen_outline()
    b.valid = True
    return b


# --------------------------------------------------------------------------- FZ / CAE

FZ_PARITY = [0, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1, 1, 0, 1, 0, 0, 0, 1,
             1, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 1, 0, 1]
CAE_PARITY = [1, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1,
              1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 1, 0, 0]


# Tabelas internas dos leitores .fz/.cae/.pcb (para abrir sem configuração).
_TBL = (
    "XC1F77@Hp1`Z+3@xu%ih7`K@3*kl6H97=>0&in}@*S>eu?!C0R0qz7P^rxu#Mv?{?V{9>>Q}o!I{BgtVnl;pltL}t#{G8EzO"
    "zrygq42&ff~#22df^^`jk(jGp-9#|Z8)B-{hlTHY3(a`O=ZTJ%kms|m5vD@9p;lv+5@G;R{sb$scnZ5o7i<vR#hEl5gxHlO8"
    "uR?gC~q3s7ittj<YKOg?|evmC+;(K~KaXX#j@eQA~=4u!u4$7{**BCDh}K(9llO>yT+`VFuy7Og`$`&C7Mea>2lL4W0@+y(1"
    "?!#B<ae>%hFM=tk-iEqN5gdU52f&o_EtPVH6o*OTwQNErzU`_0kr-mt-9X9O@9L6#=4;=U1ZxA*ZZnfWG^K=F~hF%`*|w}FQ"
    "4JC{#(3UGXE`llRk=vmZTob_HnWhc$kxNy~B@z-1~%suTYE5CoST3wk#2M}N<6XHt1RD_!j&N|?oTBj)"
)


def _tbl():
    import base64
    import struct
    import zlib
    data = base64.b85decode("".join(_TBL))
    x, ks = 0x6D2B79F5, bytearray()
    while len(ks) < len(data):
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        ks += struct.pack("<I", x)
    raw = zlib.decompress(bytes(a ^ b for a, b in zip(data, ks)))
    return list(struct.unpack_from("<44I", raw, 0)), list(struct.unpack_from("<44I", raw, 176)), \
        struct.unpack_from("<Q", raw, 352)[0]


BUILTIN_FZ_KEY, BUILTIN_CAE_KEY, BUILTIN_XZZ_KEY = _tbl()


def check_fz_key(key, parity) -> bool:
    if not key or len(key) != 44:
        return False
    for i, w in enumerate(key):
        t = w & 0xFFFFFFFF
        t ^= t >> 16; t ^= t >> 8; t ^= t >> 4; t ^= t >> 2; t ^= t >> 1
        if ((~t) & 1) != parity[i]:
            return False
    return True


def fz_decode(data: bytes, key) -> bytes:
    """Decodificação RC6 do FZ, vetorizada com numpy.

    O byte de chave de cada posição depende só dos 16 bytes CIFRADOS anteriores,
    então todas as posições podem ser calculadas em paralelo.
    """
    import numpy as np
    k = np.array(key, dtype=np.uint64).astype(np.uint32)
    src = np.frombuffer(data, dtype=np.uint8)
    n = src.size
    padded = np.concatenate([np.zeros(16, np.uint8), src])

    def word(off):
        idx = np.arange(n) + off
        return (padded[idx].astype(np.uint32)
                | (padded[idx + 1].astype(np.uint32) << 8)
                | (padded[idx + 2].astype(np.uint32) << 16)
                | (padded[idx + 3].astype(np.uint32) << 24))

    A, B, C, D = word(0), word(4), word(8), word(12)

    def rotl(a, s):
        s = s & np.uint32(31)
        return (a << s) | (a >> ((np.uint32(32) - s) & np.uint32(31)))

    with np.errstate(over="ignore"):
        B = B + k[0]
        D = D + k[1]
        five = np.uint32(5)
        for i in range(1, 21):
            t = rotl(B * (np.uint32(2) * B + np.uint32(1)), five)
            u = rotl(D * (np.uint32(2) * D + np.uint32(1)), five)
            A = rotl(A ^ t, u) + k[2 * i]
            C = rotl(C ^ u, t) + k[2 * i + 1]
            A, B, C, D = B, C, D, A
        A = A + k[42]
    out = src ^ (A & np.uint32(0xFF)).astype(np.uint8)
    return out.tobytes()


def _inflate(data: bytes) -> bytes:
    d = zlib.decompressobj()
    try:
        return d.decompress(data) + d.flush()
    except zlib.error:
        # aceita dados parciais (como o OBV faz com Z_SYNC_FLUSH)
        d = zlib.decompressobj()
        out = bytearray()
        for i in range(0, len(data), 4096):
            try:
                out += d.decompress(data[i:i + 4096])
            except zlib.error:
                break
        return bytes(out)


def _fz_is_plain(buf: bytes) -> bool:
    return len(buf) > 6 and buf[4] == 0x78 and buf[5] in (0x9C, 0xDA, 0x01, 0x5E)


def parse_fz(buf: bytes, key=None, parity=FZ_PARITY, fmt="FZ") -> RawBoard:
    if len(buf) < 8:
        raise FormatError("Arquivo FZ muito pequeno")
    if not _fz_is_plain(buf):
        builtin = BUILTIN_CAE_KEY if fmt == "CAE" else BUILTIN_FZ_KEY
        candidates = [k for k in (key, builtin) if k and len(k) == 44]
        dec = None
        for k in candidates:
            t = fz_decode(buf, k)
            if _fz_is_plain(t):
                dec = t
                break
        if dec is None:
            raise KeyRequired(
                f"Arquivo {fmt} criptografado com uma chave desconhecida. Informe a chave {fmt} "
                f"(44 valores de 32 bits) em Arquivo → Chaves de descriptografia.")
        buf = dec

    n = len(buf)
    ln = int.from_bytes(buf[n - 4:n], "little")
    if ln > n:
        raise FormatError(f"{fmt}: falha ao separar o conteúdo (chave errada?)")
    descr_size = ln
    content_size = n - descr_size + 4
    content = buf[4:4 + content_size]
    descr = buf[content_size:content_size + descr_size]
    content = _inflate(content)
    descr = _inflate(descr) if descr_size else b""
    if not content:
        raise FormatError(f"{fmt}: conteúdo vazio após descompactar (chave errada?)")

    b = RawBoard(fmt)
    mult = 1.0
    parts_id: dict[str, int] = {}
    block = 0
    lines = stringfile(content)
    for line in lines:
        line = _lstrip(line).replace(",", ".")
        if not line:
            continue
        if line == "UNIT:millimeters":
            mult = 25.4
        if line[0] == "A":
            hdr = line[2:]
            if hdr.startswith("REFDES"):
                block = 1
            elif hdr.startswith("NET_NAME"):
                block = 2
            elif hdr.startswith("TESTVIA"):
                block = 3
            else:
                block = -1
            continue
        if line[0] != "S":
            continue
        c = Cur(line, 2)
        rd = lambda: c.until("!")
        if block == 1:
            part = RPart(fix_text(rd()))
            rd(); rd()
            mirror = rd()
            part.side = BOTTOM if mirror == "YES" else TOP
            b.parts.append(part)
            parts_id[part.name] = len(b.parts)
        elif block == 2:
            pin = RPin()
            pin.net = fix_text(rd())
            pn = parts_id.get(fix_text(rd()))
            if pn is None:
                continue
            pin.part = pn
            snum = fix_text(rd())
            pin.snum = snum
            name = fix_text(rd())
            if len(snum) <= 1 and (snum == "" or snum == "0"):
                pin.name = name
            pin.x = _num(rd()) * mult
            pin.y = _num(rd()) * mult
            pin.probe = int(_num(rd()))
            r = _num(rd()) / 100
            if r < 0.5:
                r = 0.5
            pin.radius = r * mult
            pin.side = b.parts[pn - 1].side
            b.pins.append(pin)
        elif block == 3:
            c.skip(2)
            nail = RNail()
            nail.net = fix_text(rd())
            rd(); rd(); rd()
            nail.x = _num(rd()) * mult
            nail.y = _num(rd()) * mult
            nail.side = TOP if rd() == "T" else BOTTOM
            b.nails.append(nail)

    # Descrição dos componentes (part number / descrição)
    if descr:
        dlines = stringfile(descr)
        for line in dlines[2:]:
            line = _lstrip(line)
            if not line or line[0] == "s":
                continue
            cols = line.split("\t")
            if len(cols) < 4:
                continue
            desc = fix_text(cols[1].strip())
            for loc in cols[3].split():
                pid = parts_id.get(fix_text(loc))
                if pid:
                    b.parts[pid - 1].mfg = desc
    if mult != 1.0 and b.pins:
        _fz_fix_units(b, mult)
    _fz_fix_mirror(b)
    b.gen_outline()
    b.valid = block != 0
    if not b.valid:
        raise FormatError(f"{fmt}: nenhum dado reconhecido (chave errada?)")
    return b


def _fz_fix_units(b: RawBoard, mult: float):
    """Arquivos com "UNIT:millimeters": a unidade interna do programa é o mil (milésimo de polegada).
    Alguns (ASUS mais novos) dizem milímetros mas gravam em mils: uma placa de "6000 mm" não existe, então
    nesses casos os números ficam como estão. Nos outros, mm -> mils de verdade (x 39,37)."""
    xs = [p.x for p in b.pins]
    ys = [p.y for p in b.pins]
    ext = max(max(xs) - min(xs), max(ys) - min(ys)) / mult      # tamanho da placa na unidade do arquivo
    k = (1.0 if ext > 1000 else 1000.0 / 25.4) / mult           # > 1 m em "mm": na verdade são mils
    if abs(k - 1.0) < 1e-12:
        return
    for p in b.pins:
        p.x *= k
        p.y *= k
        p.radius *= k
    for n in b.nails:
        n.x *= k
        n.y *= k


def _fz_fix_mirror(b: RawBoard):
    """Lado dos componentes. Alguns FZ (ex.: placas-mãe ASUS) marcam SYM_MIRROR=YES nos componentes do lado de
    cima: quase todos ficavam no Bottom e a placa aparecia espelhada. Se mais de 85% estão marcados como
    Bottom, a marcação está invertida (o lado de baixo de uma placa nunca tem quase tudo)."""
    n = len(b.parts)
    if n < 50:
        return
    bot = sum(1 for p in b.parts if p.side == BOTTOM)
    if bot <= 0.85 * n:
        return
    swap = {TOP: BOTTOM, BOTTOM: TOP}
    for p in b.parts:
        p.side = swap.get(p.side, p.side)
    for p in b.pins:
        p.side = swap.get(p.side, p.side)


def _num(s: str) -> float:
    m = _RE_DBL.match(s)
    return float(m.group(1)) if m else 0.0


# --------------------------------------------------------------------------- XZZ PCB

XZZ_PARITY = [1, 1, 1, 1, 1, 1, 1, 0]
XZZ_SCALE = 10000


def check_xzz_key(key) -> bool:
    if key is None:
        return False
    for i in range(8):
        t = (key >> (i * 8)) & 0xFF
        t ^= t >> 4; t ^= t >> 2; t ^= t >> 1
        if ((~t) & 1) != XZZ_PARITY[i]:
            return False
    return True


def verify_xzz(buf: bytes) -> bool:
    if len(buf) < 6:
        return False
    if buf[:6] == b"XZZPCB":
        return True
    if len(buf) > 0x10 and buf[0x10] != 0:
        k = buf[0x10]
        return bytes(c ^ k for c in buf[:6]) == b"XZZPCB"
    return False


class _DesWrap:
    def __init__(self, enc, dec):
        self.encrypt = enc
        self.decrypt = dec


def des_ecb(key: int):
    """DES-ECB com pycryptodome ou, na falta dele, com 'cryptography' (3DES com K1=K2=K3 = DES)."""
    k = key.to_bytes(8, "big")
    try:
        from Crypto.Cipher import DES
        c = DES.new(k, DES.MODE_ECB)
        return _DesWrap(c.encrypt, c.decrypt)
    except ImportError:
        pass
    try:
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:
            from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        c = Cipher(TripleDES(k * 3), modes.ECB())
        return _DesWrap(lambda d: c.encryptor().update(d), lambda d: c.decryptor().update(d))
    except ImportError:
        raise FormatError("Para abrir .pcb (XZZ) instale o pacote 'pycryptodome'.")


def _u32(buf, pos):
    if pos + 4 > len(buf):
        raise FormatError("XZZ: arquivo truncado")
    return int.from_bytes(buf[pos:pos + 4], "little")


def parse_xzz(buf: bytes, key=None) -> RawBoard:
    if not key:
        key = BUILTIN_XZZ_KEY
    des = des_ecb(key)

    buf = bytearray(buf)
    marker = buf.find(b"v6v6555v6v6")
    end = marker if marker >= 0 else len(buf)
    if buf[0x10] != 0:
        k = buf[0x10]
        for i in range(end):
            buf[i] ^= k
    buf = bytes(buf)

    main_start = _u32(buf, 0x20) + 0x20
    net_start = _u32(buf, 0x28) + 0x20
    main_size = _u32(buf, main_start)
    net_size = _u32(buf, net_start)

    b = RawBoard("XZZ PCB")
    nets: dict[int, str] = {}
    nb = buf[net_start + 4: net_start + 4 + net_size]
    p = 0
    while p + 8 <= len(nb):
        sz = _u32(nb, p); idx = _u32(nb, p + 4); p += 8
        if sz < 8:
            break
        nets[idx] = fix_text(nb[p:p + sz - 8].decode("latin-1"))
        p += sz - 8

    def decrypt(block: bytes) -> bytes:
        pad = (-len(block)) % 8
        return des.decrypt(block + b"\0" * pad)[:len(block)]

    def arc(blk):
        layer, x, y, r, a0, a1 = (_u32(blk, i * 4) for i in range(6))
        if layer != 28:
            return
        cx, cy, r = x // XZZ_SCALE, y // XZZ_SCALE, r // XZZ_SCALE
        a0 //= XZZ_SCALE; a1 //= XZZ_SCALE
        if a0 > a1:
            a0, a1 = a1, a0
        if a1 - a0 > 180:
            a0 += 360
        s, e = math.radians(a0), math.radians(a1)
        steps = 9
        pold = (int(cx + r * math.cos(s)), int(cy + r * math.sin(s)))
        for i in range(1, steps + 1):
            a = s + i * (e - s) / steps
            pt = (int(cx + r * math.cos(a)), int(cy + r * math.sin(a)))
            b.outline_segments.append((pold, pt))
            pold = pt

    def line_seg(blk):
        layer, x1, y1, x2, y2 = (_u32(blk, i * 4) for i in range(5))
        if layer != 28:
            return
        b.outline_segments.append(((x1 // XZZ_SCALE, y1 // XZZ_SCALE), (x2 // XZZ_SCALE, y2 // XZZ_SCALE)))

    def part_block(enc):
        blk = decrypt(enc)
        cp = 0
        part_size = _u32(blk, cp); cp += 4 + 18
        gsz = _u32(blk, cp); cp += 4 + gsz
        if cp >= len(blk) or blk[cp] != 0x06:
            return
        cp += 31
        nsz = _u32(blk, cp); cp += 4
        name = fix_text(blk[cp:cp + nsz].decode("latin-1")); cp += nsz
        part = RPart(name, TOP, True)
        limit = min(part_size + 4, len(blk))
        while cp < limit:
            st = blk[cp]; cp += 1
            if st in (0x01, 0x05, 0x06):
                cp += _u32(blk, cp) + 4
            elif st == 0x09:
                psz = _u32(blk, cp)
                pend = cp + psz + 4
                q = cp + 8
                x = _u32(blk, q); y = _u32(blk, q + 4); q += 16
                pnsz = _u32(blk, q); q += 4
                pname = fix_text(blk[q:q + pnsz].decode("latin-1")); q += pnsz + 32
                netidx = _u32(blk, q) if q + 4 <= len(blk) else 0
                cp = pend
                pin = RPin()
                pin.side = TOP
                pin.x, pin.y = x // XZZ_SCALE, y // XZZ_SCALE
                pin.name = pin.snum = pname
                net = nets.get(netidx, "")
                pin.net = "UNCONNECTED" if net == "NC" else net
                pin.part = len(b.parts) + 1
                b.pins.append(pin)
        part.end_of_pins = len(b.pins)
        b.parts.append(part)

    def test_pad(blk):
        x = _u32(blk, 4); y = _u32(blk, 8)
        nl = _u32(blk, 20)
        name = fix_text(blk[24:24 + nl].decode("latin-1"))
        netidx = _u32(blk, len(blk) - 4)
        part = RPart("..." + name, TOP, True)
        pin = RPin()
        pin.snum = name
        pin.side = TOP
        pin.x, pin.y = x // XZZ_SCALE, y // XZZ_SCALE
        net = nets.get(netidx, "")
        pin.net = "" if net in ("UNCONNECTED", "NC") else net
        pin.part = len(b.parts) + 1
        b.pins.append(pin)
        part.end_of_pins = len(b.pins)
        b.parts.append(part)

    cp = main_start + 4
    stop = min(main_start + 4 + main_size, len(buf))
    while cp + 5 <= stop:
        bt = buf[cp]; cp += 1
        bs = _u32(buf, cp); cp += 4
        blk = buf[cp:cp + bs]
        cp += bs
        try:
            if bt == 0x01:
                arc(blk)
            elif bt == 0x05:
                line_seg(blk)
            elif bt == 0x07:
                part_block(blk)
            elif bt == 0x09:
                test_pad(blk)
        except FormatError:
            continue

    if b.outline_segments:
        mx = min(min(a[0], c[0]) for a, c in b.outline_segments)
        my = min(min(a[1], c[1]) for a, c in b.outline_segments)
        b.outline_segments = [((a[0] - mx, a[1] - my), (c[0] - mx, c[1] - my)) for a, c in b.outline_segments]
        for pin in b.pins:
            pin.x -= mx
            pin.y -= my
    b.valid = True
    return b


# --------------------------------------------------------------------------- detecção

PROPRIETARY = {
    ".tvw": "Este .tvw não tem a assinatura esperada do Tebo-ICT (pode estar corrompido).",
    ".a3p": "A3P é um formato proprietário e ainda não é suportado.",
    ".nbv": "NBV é um formato proprietário e ainda não é suportado.",
    ".gr": "Este .gr não corresponde a nenhum formato aberto conhecido.",
    ".f2b": "Este .f2b não corresponde a nenhum formato aberto conhecido.",
    ".faz": "Este .faz não corresponde a nenhum formato aberto conhecido.",
}


def load_file(path: str, keys: dict | None = None) -> RawBoard:
    keys = keys or {}
    with open(path, "rb") as f:
        buf = f.read()
    if len(buf) < 4:
        raise FormatError("Arquivo vazio ou muito pequeno.")
    ext = os.path.splitext(path)[1].lower()

    if ext == ".fz":
        return parse_fz(buf, keys.get("fz"), FZ_PARITY, "FZ")
    if ext == ".cae":
        return parse_fz(buf, keys.get("cae"), CAE_PARITY, "CAE")
    if ext in (".asc", ".bom"):
        return parse_asc(path)
    if verify_tvw(buf):
        return parse_tvw(buf)
    if verify_gencad(buf):
        return parse_gencad(buf)
    if verify_cad(buf):
        return parse_cad(buf)
    if ext == ".cst":
        return parse_cst(buf)
    if verify_brd(buf):
        return parse_brd(buf)
    if verify_brd2(buf):
        return parse_brd2(buf)
    if verify_bdv(buf):
        return parse_bdv(buf)
    if verify_bvr(buf):
        return parse_bvr(buf)
    if verify_bvr3(buf):
        return parse_bvr3(buf)
    if len(buf) >= 0xFB and buf[0xF8:0xFB] in (b"all", b"vie"):
        raise FormatError("Este é um .brd binário do Cadence Allegro (arquivo de projeto), não um boardview. "
                          "Não é suportado.")
    if verify_xzz(buf):
        return parse_xzz(buf, keys.get("xzz"))
    # Tentativas extras: variações FZ com outra extensão (.faz etc.)
    if _fz_is_plain(buf):
        try:
            return parse_fz(buf, None, FZ_PARITY, "FZ")
        except FormatError:
            pass
    if ext in (".faz", ".fz"):
        try:
            return parse_fz(buf, keys.get("fz"), FZ_PARITY, "FZ")
        except Exception:
            pass
    if ext in PROPRIETARY:
        raise FormatError(PROPRIETARY[ext])
    raise FormatError("Formato de arquivo não reconhecido.")


SUPPORTED_EXTS = [".a3p", ".asc", ".bdv", ".brd", ".bv", ".bvr", ".cad", ".cae", ".cst",
                  ".f2b", ".faz", ".fz", ".gr", ".nbv", ".pcb", ".tvw", ".bom"]


# --------------------------------------------------------------------------- TVW (Tebo-ICT)
#
# Formato descoberto por engenharia reversa (validado com arquivos reais). Estrutura (little-endian):
#   cabeçalho  : strings Pascal ofuscadas ("Tebo-ictview files.", fabricante, nome, data),
#                nº de objetos (camadas)
#   objetos    : tipo 3 = camada completa (formas, pads, linhas, arcos, superfícies...)
#                tipo 1 = camada de furação
#   redes      : u32 n, u32 n, n × string
#   ...        : cadastro de probes/fixtures (ignorado)
#   componentes: u32 n, u32 ?, n × componente; cada pino aponta para um pad
#                (índice*8) dentro da camada onde o componente está montado.
#   Coordenadas: inteiros em 1/100 mil.

_U32 = struct.Struct("<I").unpack_from
_I32 = struct.Struct("<i").unpack_from
_IIIII = struct.Struct("<IIIII").unpack_from
_III = struct.Struct("<III").unpack_from
_II = struct.Struct("<II").unpack_from


class _R:
    __slots__ = ("d", "p")

    def __init__(self, d, p=0):
        self.d = d
        self.p = p

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def u32(self):
        v = _U32(self.d, self.p)[0]
        self.p += 4
        return v

    def i32(self):
        v = _I32(self.d, self.p)[0]
        self.p += 4
        return v

    def pstr(self):
        n = self.d[self.p]
        v = self.d[self.p + 1:self.p + 1 + n]
        if len(v) < n:
            raise FormatError("TVW: fim inesperado do arquivo")
        self.p += 1 + n
        return v


def _s8(v):
    v &= 0xFF
    return v - 256 if v > 127 else v


def tvw_decode(data: bytes) -> str:
    """Decodifica os textos ofuscados do cabeçalho TVW (cifra por posição, sem chave)."""
    out = bytearray(data)
    for i, b in enumerate(data):
        c = _s8(b)
        if 97 <= c <= 106:                       # 'a'..'j' -> dígitos
            t = _s8(c - i % 3)
            d = -4 if _s8(t - 4) > 0x60 else 6
            out[i] = (-0x66 - (d + t)) & 0xFF
        elif ((c + 0x95) & 0xFF) < 0x10:          # 'k'..'z'
            t = _s8(c - i % 10)
            d = -5 if _s8(t - 5) > 106 else 11
            out[i] = (d + t) & 0xFF
        elif ((c + 0xBF) & 0xFF) < 0x1A:          # 'A'..'Z'
            t = _s8(c + 5 + i % 10)
            out[i] = (t - 26 if t >= 91 else t) & 0xFF
        elif ((c - 0x30) & 0xFF) <= 9:            # '0'..'9' -> 'a'..'j'
            t = _s8(c + 4 + i % 3)
            t = t - 10 if t >= 58 else t
            out[i] = (t + 49) & 0xFF
    return out.decode("latin-1")


def verify_tvw(buf: bytes) -> bool:
    return len(buf) > 20 and buf[1:5] == b"O95w"


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")


class _TVW:
    def __init__(self, d):
        self.r = _R(d)
        self.d = d

    def err(self, msg):
        raise FormatError(f"TVW: {msg} (posição {self.r.p})")

    def header(self):
        r = self.r
        h = {"sig": tvw_decode(r.pstr())}
        if r.u32() != 1:
            self.err("cabeçalho inválido")
        h["vendor"] = tvw_decode(r.pstr())
        if r.d[r.p] == 0:
            r.p += 1
        s = tvw_decode(r.pstr())
        if any(s.startswith(m) for m in _MONTHS) or ("," in s and any(c.isdigit() for c in s)):
            h["date"] = s
        else:
            h["name"] = s
            h["date"] = tvw_decode(r.pstr())
        b = r.d[r.p:r.p + 3]
        if b == b"\0\0\0":
            r.p += 3
        elif len(b) == 3 and b[0] == 0 and b[1] == 0 and 0 < b[2] < 0x41:
            r.p += 2
            r.pstr()
        else:
            self.err("cabeçalho inválido (const3)")
        r.p += 12
        h["nobj"] = r.u32()
        return h

    def layer_header(self):
        r = self.r
        if (r.u32(), r.u32()) != (2, 1):
            self.err("cabeçalho de camada")
        names = [r.pstr().decode("latin-1") for _ in range(3)]
        r.p += 12
        return names

    def shape(self):
        r = self.r
        if r.u32() != 1:
            self.err("forma")
        w = r.i32()
        h = r.i32()
        t = r.u32()
        if t in (0, 1, 3):
            r.p += 8
        elif t == 5:
            r.u32()
            r.pstr()
            r.p += 16
            for _ in range(r.u32()):
                st = r.u32()
                if st == 2:
                    r.p += 12
                    m = r.u32()
                    r.p += 8 * m
                elif st == 5:
                    r.p += 12 + 20
                else:
                    self.err("sub-objeto de forma")
        else:
            self.err("tipo de forma")
        return (w, h, t)

    def pads(self):
        r = self.r
        n = r.u32()
        out = []
        if n == 0:
            return out
        if r.u32() != 2:
            self.err("bloco de pads")
        d = self.d
        p = r.p
        for _ in range(n):
            net, shp, x, y = struct.unpack_from("<IIii", d, p)
            f0, f1, f2 = d[p + 16], d[p + 17], d[p + 18]
            p += 19
            if f1:
                f3 = d[p]
                p += 1
                if f2 == 1:
                    p += 12
                if f0 or f3:
                    p += 16
                g = d[p]
                p += 2
                if g:
                    p += 16
            out.append((net, shp, x, y))
        r.p = p
        return out

    def lines(self, L=None):
        """Trilhas: (rede, forma, x1, y1, x2, y2) em int32."""
        r = self.r
        n = r.u32()
        if n:
            if r.u32() != 0:
                self.err("bloco de linhas")
            if L is not None:
                L.setdefault("lines", []).append(np.frombuffer(r.d, "<i4", 6 * n, r.p).reshape(n, 6))
            r.p += 24 * n
        return n

    def arcs(self, L=None):
        """Arcos: (rede, forma, cx, cy, raio, ângulo inicial (float), varredura (float))."""
        r = self.r
        n = r.u32()
        if n:
            if r.u32() != 0:
                self.err("bloco de arcos")
            if L is not None:
                L.setdefault("arcs", []).append(bytes(r.d[r.p:r.p + 28 * n]))
            r.p += 28 * n
        return n

    def surfaces(self, L=None):
        """Áreas de cobre: polígono externo + furos, com a rede. Guarda só onde estão os pontos no arquivo
        ((posição, nº de pontos)); a conversão é feita de uma vez em _tvw_layers."""
        r = self.r
        n = r.u32()
        if n == 0:
            return
        if r.u32() != 2:
            self.err("bloco de superfícies")
        out = L.setdefault("surfaces", []) if L is not None else None
        d = r.d
        nd = len(d)
        p = r.p
        for _ in range(n):
            net, m = _II(d, p)
            p += 8
            outer = (p, m)
            p += 8 * m
            k = _II(d, p)[1]
            p += 8
            holes = []
            for _ in range(k):
                kind, c = _II(d, p)
                if kind > 1:
                    r.p = p + 4
                    self.err("recorte de superfície")
                p += 8
                holes.append((p, c))
                p += 8 * c
            if k:
                p += 4
            if p > nd:
                r.p = p
                raise IndexError("TVW: fim inesperado do arquivo")
            if out is not None:
                out.append((net, outer, holes))
        r.p = p

    def tail_a(self):
        r = self.r
        n = r.u32()
        r.u32()
        for _ in range(n):
            r.pstr()
            r.p += 39
        if n and r.u32() != 0:
            self.err("bloco auxiliar")
        if r.u32() != 7:
            self.err("bloco auxiliar (7)")

    def tail_b(self):
        r = self.r
        n = r.u32()
        r.p += 42 * n
        if r.u32() != 0 or r.u32() != 4:
            self.err("registro de fixture")
        for _ in range(2):
            k = r.u32()
            r.u32()
            r.p += 54 * k
        k = r.u32()
        flag = r.u32()
        r.p += 9 * k
        if flag == 1:
            save = r.p
            a, b, c = struct.unpack_from("<III", r.d, r.p)
            r.p += 12
            if a or b or c:
                q = save
                end = min(len(r.d), save + 0x1000)
                while q + 16 <= end:
                    if self.is_obj_start(q):
                        r.p = q
                        return
                    q += 1
                self.err("fim de registro")

    def is_obj_start(self, p):
        d = self.d
        for _ in range(4):
            if p + 16 > len(d):
                return False
            v = struct.unpack_from("<I", d, p)[0]
            if v:
                if v in (1, 3):
                    return struct.unpack_from("<II", d, p + 4) == (2, 1)
                if v == 4:
                    return struct.unpack_from("<III", d, p + 4) == (1, 0, 1)
                return False
            p += 4
        return False

    def logic(self, order, L):
        r = self.r
        if order not in (1, 2, 5):
            self.err(f"ordem de dados {order}")
        if r.u32() != 0 or r.u32() != 1:
            self.err("marcadores de dados")
        L["pads"] = self.pads()
        self.lines(L)
        self.arcs(L)
        self.surfaces(L)
        if order == 2:
            r.p += 16
            self.lines(L)
            self.arcs(L)
            if r.u32() != 0:
                self.err("fim de dados (ordem 2)")
        elif order == 5:
            self.err("ordem 5 ainda não suportada")
        self.tail_a()
        self.tail_b()

    def obj3(self):
        r = self.r
        L = {"type": 3, "pads": [], "_buf": r.d}
        L["name"] = self.layer_header()[0]
        n = r.u32()
        if n:
            if n < 10:
                self.err("contagem de formas")
            L["shapes"] = [self.shape() for _ in range(n - 10)]
            self.logic(r.u32(), L)
        else:
            v = struct.unpack_from("<III", r.d, r.p)
            if v[0] in (1, 2, 5) and v[1] == 0 and v[2] == 1:
                self.logic(r.u32(), L)
            else:
                self.tail_a()
                self.tail_b()
        return L

    def obj1(self):
        r = self.r
        L = {"type": 1, "pads": []}
        L["name"] = self.layer_header()[0]
        if r.u32() != 0 or r.u32() != 0:
            self.err("camada de furação")
        n = r.u32()
        if n:
            r.p += 29 * (n - 1)
            if r.u8() != 0:
                self.err("camada de furação (byte)")
            m = r.u32()
            r.u32()
            r.p += 16
            for _ in range(m):
                t = r.u8()
                if t == 8:
                    r.p += 16
                elif t in (10, 11):
                    r.p += 28
                else:
                    self.err("item de furação")
        return L

    def objects(self, nobj):
        objs = []
        r = self.r
        for _ in range(nobj):
            z = 0
            while True:
                v = r.u32()
                if v:
                    break
                z += 1
                if z > 8:
                    self.err("objeto esperado")
            start = r.p - 4
            try:
                if v == 3:
                    o = self.obj3()
                elif v == 1:
                    o = self.obj1()
                else:
                    self.err(f"tipo de objeto {v}")
            except (FormatError, struct.error, IndexError):
                # procura o início do próximo objeto e segue
                o = {"type": v, "pads": [], "name": "", "broken": True}
                q = start + 8
                while q + 16 < len(self.d) and not self.is_obj_start(q):
                    q += 1
                r.p = q
            objs.append(o)
        return objs

    def nets(self):
        r = self.r
        for _ in range(4):
            if r.u32() != 0:
                return self.find_nets()
        n = r.u32()
        if r.u32() != n:
            return self.find_nets()
        return [fix_text(r.pstr().decode("latin-1")) for _ in range(n)]

    def find_nets(self):
        d = self.d
        r = self.r
        for p in range(r.p, len(d) - 8):
            n, m = struct.unpack_from("<II", d, p)
            if n != m or not (2 <= n < 500000):
                continue
            q = p + 8
            ok = True
            for _ in range(min(n, 20)):
                L = d[q]
                s = d[q + 1:q + 1 + L]
                if L == 0 or any(c < 0x20 or c == 0x7F for c in s):
                    ok = False
                    break
                q += 1 + L
            if ok:
                r.p = p + 8
                return [fix_text(r.pstr().decode("latin-1")) for _ in range(n)]
        self.err("lista de redes não encontrada")

    def part(self):
        r = self.r
        name = fix_text(r.pstr().decode("latin-1"))
        r.p += 24 + 12
        if r.u32() != 0:
            self.err("componente (marcador 1)")
        r.u32()
        flag = r.u8()
        value = r.pstr()
        r.pstr()
        r.pstr()
        pkg = r.pstr()
        if flag:
            r.pstr()
            if r.u32() != 0:
                self.err("componente (marcador 2)")
        pins = []
        first = True
        while True:
            if not first and not self.looks_group():
                break
            n = r.u32()
            layer = r.u32()
            if r.u32() != 0:
                self.err("componente (marcador 3)")
            first = False
            d = r.d
            nd = len(d)
            p = r.p
            for _ in range(n):
                ref, z, _u = _III(d, p)
                if z != 0:
                    r.p = p + 8
                    self.err("pino")
                L = d[p + 12]
                pname = d[p + 13:p + 13 + L]
                if len(pname) < L:
                    r.p = p + 12
                    raise FormatError("TVW: fim inesperado do arquivo")
                p += 13 + L
                if p + 4 <= nd and _U32(d, p)[0] == 0:
                    p += 4
                pins.append((ref, pname.decode("latin-1"), layer))
            r.p = p
        mfg = " ".join(x for x in (value.decode("latin-1").strip(), pkg.decode("latin-1").strip()) if x)
        return name, mfg, pins

    def looks_group(self):
        d = self.d
        q = self.r.p
        if q + 25 > len(d):
            return False
        n, layer, z, _ref, z2 = _IIIII(d, q)
        if not (0 < n < 100000 and layer < 4096 and z == 0 and z2 == 0):
            return False
        L = d[q + 24]
        return 0 < L < 64 and all(32 <= c < 127 for c in d[q + 25:q + 25 + L])

    def find_parts(self):
        d = self.d
        start = self.r.p
        for m in re.finditer(rb"[\x01-\x40]", d[start:]):
            q = start + m.start() - 8
            if q < start:
                continue
            cnt = struct.unpack_from("<I", d, q)[0]
            if not (0 < cnt < 500000):
                continue
            L = d[q + 8]
            if not all(33 <= c < 127 for c in d[q + 9:q + 9 + L]):
                continue
            self.r.p = q + 8
            try:
                for _ in range(min(cnt, 5)):
                    self.part()
            except (FormatError, struct.error, IndexError):
                continue
            self.r.p = q + 8
            return cnt
        self.err("bloco de componentes não encontrado")


def parse_tvw(buf: bytes) -> RawBoard:
    t = _TVW(buf)
    h = t.header()
    if not h["sig"].startswith("Tebo"):
        raise FormatError("Não é um arquivo TVW (assinatura inválida).")
    objs = t.objects(h["nobj"])
    nets = t.nets()
    cnt = t.find_parts()

    names = [o.get("name", "").upper() for o in objs]
    top_i = names.index("TOP") if "TOP" in names else None
    bot_i = names.index("BOTTOM") if "BOTTOM" in names else None

    b = RawBoard("TVW")
    xs, ys = [], []
    for _ in range(cnt):
        try:
            name, mfg, pins = t.part()
        except (FormatError, struct.error, IndexError):
            break
        layers = {lay for _, _, lay in pins}
        if layers and layers <= {top_i}:
            side = TOP
        elif layers and layers <= {bot_i}:
            side = BOTTOM
        else:
            side = BOTH
        part = RPart(name, side, smd=(side != BOTH))
        part.mfg = mfg
        b.parts.append(part)
        pidx = len(b.parts)
        for ref, pname, lay in pins:
            if lay >= len(objs):
                continue
            pads = objs[lay]["pads"]
            i = ref // 8
            if i >= len(pads):
                continue
            net_i, shp, x, y = pads[i]
            pin = RPin()
            pin.x = x / 100.0
            pin.y = y / 100.0
            shapes = objs[lay].get("shapes") or []
            if 0 <= shp - 10 < len(shapes):
                w, h, st = shapes[shp - 10]
                if w > 0 and h > 0:
                    # 0 = redondo, 1 = retangular, 3 = oblongo, 5 = forma livre (usa o retângulo envolvente)
                    pin.pad = (pin.x, pin.y, w / 200.0, h / 200.0, 1.0, 0.0, 1.0 if st in (0, 3) else 0.0)
            pin.part = pidx
            pin.snum = pname
            pin.name = pname
            pin.net = nets[net_i] if net_i < len(nets) else ""
            pin.side = TOP if lay == top_i else BOTTOM if lay == bot_i else side
            b.pins.append(pin)
            xs.append(pin.x)
            ys.append(pin.y)
        part.end_of_pins = len(b.pins)
    if not b.pins:
        raise FormatError("TVW: nenhum pino encontrado.")
    try:
        _tvw_layers(b, objs, nets)
    except Exception:          # camadas são extra: se algo der errado, a placa abre sem elas
        b.layers = []
    m = (max(xs) - min(xs) + max(ys) - min(ys)) * 0.01
    b.gen_outline(margin=m)
    b.valid = True
    return b


def _tvw_layers(b, objs, nets):
    """Camadas de cobre do TVW (as que têm nome e algum conteúdo), em mils."""
    b.layer_nets = list(nets)
    for o in objs:
        name = o.get("name", "")
        if not name or o.get("broken"):
            continue
        shapes = o.get("shapes") or []
        sw = np.array([sh[0] for sh in shapes] or [0], dtype=np.float64) / 100.0
        sh_ = np.array([sh[1] for sh in shapes] or [0], dtype=np.float64) / 100.0
        st = np.array([sh[2] for sh in shapes] or [0])

        def width(idx):
            k = np.asarray(idx, dtype=np.int64) - 10
            ok = (k >= 0) & (k < len(shapes))
            out = np.zeros(len(k))
            out[ok] = sw[k[ok]]
            return out
        L = RawLayer(name)
        for a in o.get("lines", []):
            seg = np.empty((len(a), 6))
            seg[:, 0:4] = a[:, 2:6] / 100.0
            seg[:, 4] = width(a[:, 1])
            seg[:, 5] = a[:, 0]
            L.segs.append(seg)
        for raw in o.get("arcs", []):
            n = len(raw) // 28
            ints = np.frombuffer(raw, "<i4").reshape(n, 7)
            flts = np.frombuffer(raw, "<f4").reshape(n, 7)
            L.add_arcs(ints[:, 2] / 100.0, ints[:, 3] / 100.0, ints[:, 4] / 100.0,
                       flts[:, 5], flts[:, 6], width(ints[:, 1]), ints[:, 0])
        surfs = o.get("surfaces") or []
        if surfs:
            # todos os pontos de uma vez: junta os pedaços do arquivo, converte e devolve fatias
            d = o["_buf"]
            ranges = []
            for net, (po, m), holes in surfs:
                if m >= 3:
                    ranges.append((po, m))
                    ranges.extend(h for h in holes if h[1] >= 3)
            allpts = np.frombuffer(b"".join(d[po:po + 8 * c] for po, c in ranges), "<i4").reshape(-1, 2) / 100.0
            q = 0
            for net, (po, m), holes in surfs:
                if m < 3:
                    continue
                outer = allpts[q:q + m]
                q += m
                hs = []
                for _, c in holes:
                    if c >= 3:
                        hs.append(allpts[q:q + c])
                        q += c
                L.polys.append((int(net), outer, hs))
        pads = o.get("pads") or []
        if pads and shapes:
            arr = np.array([(p[0], p[1], p[2], p[3]) for p in pads], dtype=np.float64)
            k = arr[:, 1].astype(np.int64) - 10
            ok = (k >= 0) & (k < len(shapes))
            arr, k = arr[ok], k[ok]
            if len(arr):
                pd = np.empty((len(arr), 6))
                pd[:, 0] = arr[:, 2] / 100.0
                pd[:, 1] = arr[:, 3] / 100.0
                pd[:, 2] = sw[k] / 2
                pd[:, 3] = sh_[k] / 2
                pd[:, 4] = np.isin(st[k], (0, 3)).astype(np.float64)
                pd[:, 5] = arr[:, 0]
                L.pads.append(pd)
        if not L.empty() and (L.segs or L.polys):
            b.layers.append(L)


# --------------------------------------------------------------------------- GenCAD

def _gencad_layers(b, routes, pad_of, scale):
    """Trilhas e vias do $ROUTES (TOP, camadas internas, BOTTOM)."""
    if not routes:
        return
    net_ix: dict[str, int] = {}
    raw_ix: dict[str, int] = {}          # nome como veio no arquivo -> índice (evita fix_text repetido)

    def nix(name):
        i = raw_ix.get(name)
        if i is not None:
            return i
        fixed = fix_text(name)
        i = net_ix.get(fixed)
        if i is None:
            i = net_ix[fixed] = len(b.layer_nets)
            b.layer_nets.append(fixed)
        raw_ix[name] = i
        return i
    names = [n for n in routes if n != "*VIAS*"]
    order = sorted(names, key=lambda n: (0 if n == "TOP" else 2 if n == "BOTTOM" else 1, n))
    vias = []
    for ref, x, y, net in routes.get("*VIAS*", {}).get("via", []):
        g, _rot = pad_of(ref)
        if g is None:
            continue
        vias.append((x * scale, y * scale, g[2] * scale, g[3] * scale, g[4] if g[4] else 1.0, nix(net)))
    via_arr = np.array(vias, dtype=np.float64).reshape(-1, 6)
    for name in order:
        R = routes[name]
        L = RawLayer(name)
        if R["seg"]:
            seg = np.array([(a, b_, c, d, w, nix(n)) for a, b_, c, d, w, n in R["seg"]], dtype=np.float64)
            seg[:, 0:5] *= scale
            L.segs.append(seg)
        if R["arc"]:
            A = np.array([a[:7] for a in R["arc"]], dtype=np.float64)
            nets_ = np.array([nix(a[7]) for a in R["arc"]], dtype=np.float64)
            x1, y1, x2, y2, xc, yc, w = (A[:, i] for i in range(7))
            r = np.hypot(x1 - xc, y1 - yc)
            a0 = np.degrees(np.arctan2(y1 - yc, x1 - xc))
            a1 = np.degrees(np.arctan2(y2 - yc, x2 - xc))
            sweep = (a1 - a0) % 360.0          # GenCAD: sentido anti-horário
            sweep[sweep == 0] = 360.0
            L.add_arcs(xc * scale, yc * scale, r * scale, a0, sweep, w * scale, nets_)
        if len(via_arr):
            L.pads.append(via_arr)
        if L.segs:
            b.layers.append(L)


def verify_gencad(buf: bytes) -> bool:
    return b"GENCAD" in buf[:2000] and b"$HEADER" in buf[:2000]


def parse_gencad(buf: bytes) -> RawBoard:
    text = buf.decode("latin-1")
    section = None
    shapes: dict[str, list] = {}
    shape_th: dict[str, bool] = {}
    comps: list[dict] = []
    signals: dict[tuple, str] = {}
    outline = []
    cur_shape = None
    cur_comp = None
    cur_sig = None
    scale = 1.0
    last = None
    devices: dict[str, str] = {}
    board_skip = False
    pads: dict[str, tuple] = {}          # nome -> (cx, cy, hw, hh, arredondamento)
    pad_pts: dict[str, list] = {}
    pad_kind: dict[str, str] = {}
    cur_pad = None
    padstacks: dict[str, tuple] = {}     # nome -> (pad, rotação)
    cur_ps = None
    tracks: dict[str, float] = {}        # largura das trilhas
    routes: dict[str, dict] = {}         # camada -> {"seg": [...], "arc": [...], "via": [...]}
    route_net = ""
    route_track = ""
    route_layer = "TOP"

    def toks_q(line):
        out, i, n = [], 0, len(line)
        while i < n:
            while i < n and line[i] in " \t":
                i += 1
            if i >= n:
                break
            if line[i] == '"':
                j = line.find('"', i + 1)
                j = n if j < 0 else j
                out.append(line[i + 1:j])
                i = j + 1
            else:
                j = i
                while j < n and line[j] not in " \t":
                    j += 1
                out.append(line[i:j])
                i = j
        return out

    def toks(line):
        # caminho rápido (quase todas as linhas): sem aspas, separa só por espaço/tab, como o lento
        if '"' in line:
            return toks_q(line)
        if "\t" in line:
            line = line.replace("\t", " ")
        return [x for x in line.split(" ") if x]

    route_cur = None                     # baldes da camada atual das rotas (evita criar um dict por linha)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("$"):
            section = line[1:].upper()
            continue
        t = toks(line)
        k = t[0].upper()
        if section == "ROUTES":
            try:
                if k == "LINE":
                    if len(t) >= 5:
                        if route_cur is None:
                            route_cur = routes.setdefault(route_layer, {"seg": [], "arc": [], "via": []})
                        route_cur["seg"].append((float(t[1]), float(t[2]), float(t[3]), float(t[4]),
                                                 tracks.get(route_track, 0.0), route_net))
                elif k == "ARC":
                    if len(t) >= 7:
                        if route_cur is None:
                            route_cur = routes.setdefault(route_layer, {"seg": [], "arc": [], "via": []})
                        route_cur["arc"].append((float(t[1]), float(t[2]), float(t[3]), float(t[4]), float(t[5]),
                                                 float(t[6]), tracks.get(route_track, 0.0), route_net))
                elif k == "VIA" and len(t) >= 5:
                    routes.setdefault("*VIAS*", {"seg": [], "arc": [], "via": []})["via"].append(
                        (t[1], float(t[2]), float(t[3]), route_net))
                elif k == "ROUTE":
                    route_net = t[1] if len(t) >= 2 else ""
                elif k == "TRACK" and len(t) >= 2:
                    route_track = t[1]
                elif k == "LAYER" and len(t) >= 2:
                    route_layer = t[1].upper()
                    route_cur = None
            except ValueError:
                pass
            continue
        if section == "HEADER":
            if k == "UNITS" and len(t) >= 2:
                u = t[1].upper()
                if u == "USER" and len(t) >= 3:
                    try:
                        scale = 1000.0 / float(t[2])
                    except ValueError:
                        pass
                elif u in ("MM", "USERMM"):
                    scale = 1000.0 / 25.4 if u == "MM" else (1000.0 / 25.4) / (float(t[2]) if len(t) > 2 else 1)
                elif u == "INCH":
                    scale = 1000.0
                elif u == "THOU":
                    scale = 1.0
        elif section == "BOARD":
            if k in ("ARTWORK", "MASK", "ATTRIBUTE", "FIDUCIAL"):
                board_skip = True
                continue
            if k == "CUTOUT":
                board_skip = False
                continue
            if board_skip:
                continue
            try:
                if k == "LINE" and len(t) >= 5:
                    a = (float(t[1]), float(t[2]))
                    c = (float(t[3]), float(t[4]))
                    outline.append((a, c))
                elif k == "ARC" and len(t) >= 7:
                    a = (float(t[1]), float(t[2]))
                    c = (float(t[3]), float(t[4]))
                    ctr = (float(t[5]), float(t[6]))
                    r_ = math.hypot(a[0] - ctr[0], a[1] - ctr[1])
                    a0 = math.atan2(a[1] - ctr[1], a[0] - ctr[0])
                    a1 = math.atan2(c[1] - ctr[1], c[0] - ctr[0])
                    if a1 <= a0:
                        a1 += 2 * math.pi
                    steps = max(4, int((a1 - a0) / 0.15))
                    prev = a
                    for s in range(1, steps + 1):
                        ang = a0 + (a1 - a0) * s / steps
                        pt = (ctr[0] + r_ * math.cos(ang), ctr[1] + r_ * math.sin(ang))
                        outline.append((prev, pt))
                        prev = pt
                elif k == "CIRCLE" and len(t) >= 4:
                    cx, cy, r_ = float(t[1]), float(t[2]), float(t[3])
                    prev = (cx + r_, cy)
                    for s in range(1, 33):
                        ang = 2 * math.pi * s / 32
                        pt = (cx + r_ * math.cos(ang), cy + r_ * math.sin(ang))
                        outline.append((prev, pt))
                        prev = pt
            except ValueError:
                pass
        elif section == "PADS":
            try:
                if k == "PAD" and len(t) >= 2:
                    cur_pad = t[1]
                    pad_kind[cur_pad] = t[2].upper() if len(t) > 2 else ""
                    pad_pts[cur_pad] = []
                elif cur_pad is not None:
                    if k == "RECTANGLE" and len(t) >= 5:
                        x, y, w, h = map(float, t[1:5])
                        pad_pts[cur_pad] += [(x, y), (x + w, y + h)]
                    elif k == "CIRCLE" and len(t) >= 4:
                        x, y, r_ = map(float, t[1:4])
                        pad_pts[cur_pad] += [(x - r_, y - r_), (x + r_, y + r_)]
                        if pad_kind[cur_pad] not in ("ROUND", "FINGER", "BULLET"):
                            pad_kind[cur_pad] = "ROUND"
                    elif k in ("LINE", "ARC") and len(t) >= 5:
                        pad_pts[cur_pad] += [(float(t[1]), float(t[2])), (float(t[3]), float(t[4]))]
                        if k == "ARC" and len(t) >= 7:
                            cx_, cy_ = float(t[5]), float(t[6])
                            r_ = math.hypot(float(t[1]) - cx_, float(t[2]) - cy_)
                            pad_pts[cur_pad] += [(cx_ - r_, cy_ - r_), (cx_ + r_, cy_ + r_)]
            except ValueError:
                pass
        elif section == "TRACKS":
            if k == "TRACK" and len(t) >= 3:
                try:
                    tracks[t[1]] = float(t[2])
                except ValueError:
                    pass
        elif section == "PADSTACKS":
            if k == "PADSTACK" and len(t) >= 2:
                cur_ps = t[1]
            elif k == "PAD" and cur_ps is not None and len(t) >= 3:
                lay = t[2].upper()
                try:
                    rot = float(t[3]) if len(t) > 3 else 0.0
                except ValueError:
                    rot = 0.0
                prio = {"TOP": 0, "ALL": 1, "BOTTOM": 2}.get(lay, 9)
                if cur_ps not in padstacks or prio < padstacks[cur_ps][2]:
                    padstacks[cur_ps] = (t[1], rot, prio)
        elif section == "SHAPES":
            if k == "SHAPE" and len(t) >= 2:
                cur_shape = t[1]
                shapes.setdefault(cur_shape, [])
            elif k == "PIN" and cur_shape is not None and len(t) >= 6:
                try:
                    prot = 0.0
                    if len(t) > 6:
                        try:
                            prot = float(t[6])
                        except ValueError:
                            prot = 0.0
                    shapes[cur_shape].append((t[1], float(t[3]), float(t[4]), t[5].upper() if len(t) > 5 else "TOP",
                                              t[2], prot))
                except ValueError:
                    pass
            elif k == "INSERT" and cur_shape is not None and len(t) >= 2:
                shape_th[cur_shape] = t[1].upper() in ("TH", "THRU", "TH_SMD")
        elif section == "COMPONENTS":
            if k == "COMPONENT" and len(t) >= 2:
                cur_comp = {"name": t[1], "x": 0.0, "y": 0.0, "layer": "TOP", "rot": 0.0,
                            "shape": None, "mx": False, "my": False, "flip": False, "device": ""}
                comps.append(cur_comp)
            elif cur_comp is not None:
                try:
                    if k == "PLACE" and len(t) >= 3:
                        cur_comp["x"], cur_comp["y"] = float(t[1]), float(t[2])
                    elif k == "LAYER" and len(t) >= 2:
                        cur_comp["layer"] = t[1].upper()
                    elif k == "ROTATION" and len(t) >= 2:
                        cur_comp["rot"] = float(t[1])
                    elif k == "SHAPE" and len(t) >= 2:
                        cur_comp["shape"] = t[1]
                        rest = [x.upper() for x in t[2:]]
                        cur_comp["mx"] = "MIRRORX" in rest
                        cur_comp["my"] = "MIRRORY" in rest
                        cur_comp["flip"] = "FLIP" in rest
                    elif k == "DEVICE" and len(t) >= 2:
                        cur_comp["device"] = t[1]
                except ValueError:
                    pass
        elif section == "SIGNALS":
            if k == "SIGNAL":
                cur_sig = t[1] if len(t) >= 2 else ""
            elif k == "NODE" and cur_sig is not None and len(t) >= 3:
                signals[(t[1], t[2])] = cur_sig
        elif section == "DEVICES":
            if k == "DEVICE" and len(t) >= 2:
                last = t[1]
            elif k == "PART" and last and len(t) >= 2:
                devices[last] = " ".join(t[1:])


    # geometria dos pads (no sistema do pad)
    for nm, pts in pad_pts.items():
        if not pts:
            continue
        xs_ = [q[0] for q in pts]
        ys_ = [q[1] for q in pts]
        hw_, hh_ = (max(xs_) - min(xs_)) / 2, (max(ys_) - min(ys_)) / 2
        if hw_ <= 0 or hh_ <= 0:
            continue
        kind = pad_kind.get(nm, "")
        rnd = 1.0 if kind in ("ROUND", "FINGER", "BULLET") else 0.0
        pads[nm] = ((max(xs_) + min(xs_)) / 2, (max(ys_) + min(ys_)) / 2, hw_, hh_, rnd)

    rx_name = re.compile(r"_(\d{3})X(\d{3})(RR|R)?(?:_|$)", re.I)

    def pad_of(ref):
        ps = padstacks.get(ref)
        g = None
        rot = 0.0
        if ps is not None:
            g, rot = pads.get(ps[0]), ps[1]
        elif ref in pads:
            g = pads[ref]
        if g is None:
            # padstack sem geometria: alguns CADs põem a medida no nome (ex.: PAD_PSM_070X125RR = 0,70 x 1,25 mm)
            m = rx_name.search(ref or "")
            if m:
                k = 1.0 / 0.0254 / 100.0 / scale          # centésimos de mm -> unidade do arquivo
                g = (0.0, 0.0, int(m.group(1)) * k / 2, int(m.group(2)) * k / 2,
                     0.35 if (m.group(3) or "").upper() == "RR" else 0.0)
        return (g, rot) if g else (None, 0.0)

    # Variante "XY/TESTCAD": pinos da forma já em coordenadas absolutas
    absolute = False
    probe = []
    for c in comps[:400]:
        pins = shapes.get(c["shape"] or "", [])
        if len(pins) >= 1:
            cx = sum(p[1] for p in pins) / len(pins)
            cy = sum(p[2] for p in pins) / len(pins)
            spread = max(max(p[1] for p in pins) - min(p[1] for p in pins),
                         max(p[2] for p in pins) - min(p[2] for p in pins), 1e-9)
            probe.append(math.hypot(cx, cy) > 20 * spread and math.hypot(cx, cy) > 3 * math.hypot(c["x"], c["y"]) + 1)
    if probe and sum(probe) > 0.6 * len(probe):
        absolute = True

    # nomes com sufixo _T/_B (um componente por lado): junta em um só
    names = [c["name"] for c in comps]
    suffixed = names and sum(1 for n in names if n.endswith(("_T", "_B"))) > 0.8 * len(names)

    b = RawBoard("GenCAD")
    index: dict[str, int] = {}
    seen: set = set()
    for c in comps:
        name = c["name"]
        side = BOTTOM if (c["layer"].startswith("BOT") or c["flip"]) else TOP
        disp = name
        if suffixed and name.endswith(("_T", "_B")):
            disp = name[:-2]
            side = TOP if name.endswith("_T") else BOTTOM
        key = (name, c["shape"])
        if key in seen:          # mesmo componente repetido (variante TESTCAD)
            continue
        seen.add(key)
        pins = shapes.get(c["shape"] or "", [])
        if disp in index:
            pidx = index[disp]
            part = b.parts[pidx - 1]
            if part.side != side:
                part.side = BOTH
                part.smd = False
        else:
            part = RPart(fix_text(disp), side, smd=not shape_th.get(c["shape"] or "", False))
            if part.smd is False:
                part.side = BOTH
            part.mfg = devices.get(c["device"], "")
            part.footprint = c["shape"] or ""
            b.parts.append(part)
            pidx = len(b.parts)
            index[disp] = pidx
        # mesma convenção do OpenBoardView para espelhamento/rotação
        ang = math.radians(c["rot"])
        sx = -1 if c["mx"] else 1
        sy = -1 if c["my"] else 1
        if sx * sy == -1:
            ang = math.pi - ang
        ca, sa = math.cos(ang), math.sin(ang)
        if absolute:
            ca, sa, sx, sy = 1.0, 0.0, 1, 1

        def tf(px, py, absolute=absolute, c=c, sx=sx, sy=sy, ca=ca, sa=sa):
            if absolute:
                return px, py
            return c["x"] + sx * (px * ca - py * sa), c["y"] + sy * (px * sa + py * ca)

        for pname, px, py, _lay, padref, prot in pins:
            x, y = tf(px, py)
            pin = RPin()
            pin.x = x * scale
            pin.y = y * scale
            g, psrot = pad_of(padref)
            if g is not None:
                th_ = math.radians(prot + psrot)
                ct, st_ = math.cos(th_), math.sin(th_)
                ox, oy = g[0] * ct - g[1] * st_, g[0] * st_ + g[1] * ct
                qx, qy = tf(px + ox, py + oy)
                ax_, ay_ = sx * (ct * ca - st_ * sa), sy * (ct * sa + st_ * ca)
                ln_ = math.hypot(ax_, ay_) or 1.0
                pin.pad = (qx * scale, qy * scale, g[2] * scale, g[3] * scale, ax_ / ln_, ay_ / ln_, g[4])
            pin.part = pidx
            pin.snum = pname
            pin.name = pname
            pin.side = side if part.smd else BOTH
            pin.net = fix_text(signals.get((name, pname), "") or "")
            b.pins.append(pin)
        part.end_of_pins = len(b.pins)

    # pinos precisam ficar agrupados por componente (o modelo assume isso)
    b.pins.sort(key=lambda p: p.part)
    try:
        _gencad_layers(b, routes, pad_of, scale)
    except Exception:          # camadas são extra: se algo der errado, a placa abre sem elas
        b.layers = []
    if outline:
        b.outline_segments = [((a[0] * scale, a[1] * scale), (c[0] * scale, c[1] * scale)) for a, c in outline]
    else:
        b.gen_outline(margin=50)
    b.valid = bool(b.pins)
    if not b.valid:
        raise FormatError("GenCAD: nenhum pino encontrado.")
    return b

