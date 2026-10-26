"""Ferramentas de diagnóstico (sem interface): busca por valor/encapsulamento/coringa, caçar curto,
seguir a rede através de componentes de passagem e comparar duas revisões da mesma placa."""
from __future__ import annotations

import fnmatch
import math
import re

import numpy as np

from .info import value_of, package_of, CHIP_SIZES
from .parsers import TOP, BOTTOM, BOTH
from .theme import component_type

# ------------------------------------------------------------------ valores

_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9,
         "P": 1e-12, "N": 1e-9, "U": 1e-6, "g": 1e9, "r": 1.0, "R": 1.0, "": 1.0}
_UNIT_KIND = {"F": "F", "H": "H", "OHM": "R", "OHMS": "R", "Ω": "R", "R": "R"}
_RX_NUM = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([pnuµmkKMGRr]?)\s*(F|H|OHMS?|Ω)?\s*$", re.I)
_RX_RNOT = re.compile(r"^\s*(\d+)([pnuµRrKkM])(\d+)\s*(F|H|OHMS?|Ω)?\s*$")      # 4R7, 2K2, 4n7, 0R


def parse_value(text):
    """'0.47uF' -> (4.7e-7, 'F'); '10k' -> (1e4, None); '4R7' -> (4.7, 'R'); senão None."""
    if not text:
        return None
    t = text.strip().replace("ohm", "OHM").replace("Ohm", "OHM")
    m = _RX_RNOT.match(t)
    if m:
        a, mult, b, unit = m.groups()
        v = float(f"{a}.{b}") * _MULT.get(mult, 1.0)
        kind = _UNIT_KIND.get((unit or "").upper()) or ("R" if mult in "Rr" else None)
        return v, kind
    m = _RX_NUM.match(t)
    if not m:
        return None
    num, mult, unit = m.groups()
    if unit and unit.upper() == "F" and mult == "M":
        mult = "m"        # 1MF não existe: é mili
    try:
        v = float(num.replace(",", ".")) * _MULT.get(mult, 1.0)
    except ValueError:
        return None
    kind = _UNIT_KIND.get((unit or "").upper())
    if kind is None and mult in ("R", "r"):
        kind = "R"
    return v, kind


_KIND_OF_TYPE = {"CapacitorColor": "F", "ResistorColor": "R", "InductorColor": "H"}


def part_value(part, ptype):
    """Valor numérico do componente (ou None) e o tipo da grandeza (F, R, H)."""
    txt = value_of(part, ptype)
    if not txt:
        return None
    pv = parse_value(re.sub(r"\s+", "", txt))
    if pv is None:
        return None
    v, kind = pv
    if ptype == "ResistorColor" and txt.strip() in ("0", "0R", "0r"):
        v = 0.0
    if ptype == "ResistorColor":
        m = re.match(r"^\s*\d+(?:[.,]\d+)?\s*m(?!\w*F)", txt)
        if m:
            # "m" minúsculo em resistor: shunt (2512, nome RS…) é miliohm; resistor pequeno é megaohm
            pkg = package_of(part)
            shunt = pkg in ("1206", "1210", "1812", "2010", "2512") or re.match(r"^P?RS", part.name, re.I)
            if not shunt:
                v = v * 1e9           # 1e-3 -> 1e6
    return v, kind or _KIND_OF_TYPE.get(ptype)


def same_value(a, b):
    if a == b:
        return True
    if a == 0 or b == 0:
        return False
    return abs(a - b) <= 1e-3 * max(abs(a), abs(b))


# ------------------------------------------------------------------ busca

PKG_CODES = set(CHIP_SIZES)


def search(board, ptypes, query):
    """Interpreta a busca e devolve (descrição, [componentes], [redes]).
    Formatos: coringa (U5*, C1?2), valor (0.47uF, 470n, 10k, 4R7), encapsulamento (0402, pkg:QFN),
    'valor:' / 'pkg:' forçam o tipo. Sem nada disso: nome exato ou parte do nome."""
    q = query.strip()
    if not q:
        return "", [], []
    b = board
    low = q.lower()
    if low.startswith(("pkg:", "enc:", "encapsulamento:")):
        pk = q.split(":", 1)[1].strip().upper()
        res = [i for i, p in enumerate(b.parts) if pk and pk in (package_of(p) or "").upper()
               or pk and pk in (p.footprint or "").upper()]
        return f"encapsulamento {pk}", res, []
    if low.startswith(("valor:", "val:", "value:")):
        q = q.split(":", 1)[1].strip()
        return _search_value(b, ptypes, q, forced=True)
    if any(ch in q for ch in "*?["):
        pat = q.upper()
        parts = [i for i, p in enumerate(b.parts) if fnmatch.fnmatchcase(p.name.upper(), pat)]
        nets = [i for i, n in enumerate(b.nets) if n != "UNCONNECTED" and fnmatch.fnmatchcase(n.upper(), pat)]
        return f"“{q}”", parts, nets
    if q.upper() in b.part_index or q in b.net_index:
        return "", [], []              # nome exato: a busca normal resolve
    if q in PKG_CODES:
        res = [i for i, p in enumerate(b.parts) if package_of(p) == q]
        return f"encapsulamento {q}", res, []
    if q[0].isdigit():
        r = _search_value(b, ptypes, q)
        if r[1]:
            return r
    return "", [], []


def _search_value(b, ptypes, q, forced=False):
    pv = parse_value(q.replace(" ", ""))
    if pv is None:
        return (f"valor “{q}” (não entendi)", [], []) if forced else ("", [], [])
    v, kind = pv
    res = []
    for i, p in enumerate(b.parts):
        x = part_value(p, ptypes[i])
        if x is None:
            continue
        pvv, pk = x
        if kind and pk and kind != pk:
            continue
        if same_value(pvv, v):
            res.append(i)
    unit = {"F": "F", "R": "Ω", "H": "H"}.get(kind or "", "")
    return f"valor {q}{'' if unit and q.upper().endswith(unit.upper()) else ''}", res, []


# ------------------------------------------------------------------ seguir a rede

_PASS_NAMES = re.compile(r"^(P?VS|P?FB|P?JP|PJ|P?SH|SHORT|P?RS|NT)\d", re.I)
_ZERO = re.compile(r"(^|[^0-9.])0\s*(R|OHM|Ω)?($|[^0-9.])|JUMPER|SHORT|0R\b", re.I)


def is_passthrough(board, pi, ptype):
    """Componente de 2 pinos que deixa a alimentação passar: indutor, fusível, jumper, resistor de 0 Ω
    (ou de valor muito baixo, como shunt de corrente), ferrite, curto virtual."""
    p = board.parts[pi]
    if len(p.pins) != 2:
        return False
    if ptype in ("InductorColor", "FuseColor", "JumperColor"):
        return True
    if _PASS_NAMES.match(p.name):
        return True
    if ptype == "ResistorColor":
        x = part_value(p, ptype)
        if x is not None and x[0] <= 1.0:
            return True
        if x is None and p.mfg and _ZERO.search(p.mfg):
            return True
    return False


def follow_net(board, ptypes, start, max_nets=300):
    """Redes ligadas à 'start' através de componentes de passagem.
    Devolve lista de (rede, profundidade, componente que liga, rede anterior)."""
    b = board
    if start is None or b.net_is_gnd[start] or b.net_is_nc[start]:
        return [(start, 0, None, None)] if start is not None else []
    # rede -> componentes de passagem (pré-calculado uma vez por placa)
    cache = getattr(b, "_pass_cache", None)
    if cache is None:
        cache = {}
        for pi, p in enumerate(b.parts):
            if is_passthrough(b, pi, ptypes[pi]):
                n0, n1 = int(b.net[p.pins[0]]), int(b.net[p.pins[1]])
                if n0 != n1:
                    cache.setdefault(n0, []).append((pi, n1))
                    cache.setdefault(n1, []).append((pi, n0))
        b._pass_cache = cache
    out = [(start, 0, None, None)]
    seen = {start}
    frontier = [start]
    depth = 0
    while frontier and len(out) < max_nets:
        depth += 1
        nxt = []
        for n in frontier:
            for pi, m in cache.get(n, ()):
                if m in seen or b.net_is_gnd[m] or b.net_is_nc[m]:
                    continue
                seen.add(m)
                out.append((m, depth, pi, n))
                nxt.append(m)
        frontier = nxt
    return out


# ------------------------------------------------------------------ caçar curto

def caps_on_nets(board, ptypes, nets):
    """Capacitores com algum pino nas redes dadas. Devolve lista de dict ordenada para percorrer:
    por lado e, dentro do lado, num caminho curto (vizinho mais próximo)."""
    b = board
    nets = set(nets)
    found = {}
    for n in nets:
        for i in b.net_pins[n]:
            pi = int(b.part[i])
            if pi < 0 or pi in found or ptypes[pi] != "CapacitorColor":
                continue
            p = b.parts[pi]
            others = [int(b.net[j]) for j in p.pins if int(b.net[j]) not in nets]
            other = b.nets[others[0]] if others else (b.nets[n] if len(p.pins) > 1 else "")
            found[pi] = {"pi": pi, "name": p.name, "side": p.side, "value": value_of(p, ptypes[pi]) or "",
                         "other": other, "to_gnd": bool(others) and bool(b.net_is_gnd[others[0]]),
                         "net": b.nets[n]}
    rows = []
    for side in (TOP, BOTTOM, BOTH):
        items = [r for r in found.values() if r["side"] == side]
        rows += _tour(b, items)
    return rows


def _tour(b, items):
    """Ordem de visita: começa no canto de cima/esquerda e vai sempre para o mais próximo."""
    if len(items) <= 2:
        return items
    pts = np.array([b.parts[r["pi"]].center for r in items], dtype=np.float64)
    left = list(range(len(items)))
    cur = int(np.argmin(pts[:, 0] - pts[:, 1]))
    order = [cur]
    left.remove(cur)
    while left:
        rest = np.array(left)
        d = np.hypot(pts[rest, 0] - pts[cur, 0], pts[rest, 1] - pts[cur, 1])
        cur = int(rest[np.argmin(d)])
        order.append(cur)
        left.remove(cur)
    return [items[k] for k in order]


# ------------------------------------------------------------------ comparar revisões

def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().upper())


def compare_boards(a, b, ptypes_a=None, ptypes_b=None):
    """Compara a placa 'a' (aberta) com 'b' (a outra revisão). Devolve lista de
    (categoria, nome, detalhe, índice do componente em 'a' ou None)."""
    pa = {p.name.upper(): i for i, p in enumerate(a.parts)}
    pb = {p.name.upper(): i for i, p in enumerate(b.parts)}
    ptypes_a = ptypes_a or [component_type(p.name) for p in a.parts]
    ptypes_b = ptypes_b or [component_type(p.name) for p in b.parts]
    out = []
    only_a = sorted(set(pa) - set(pb))
    only_b = sorted(set(pb) - set(pa))
    common = sorted(set(pa) & set(pb))
    for n in only_a:
        p = a.parts[pa[n]]
        out.append(("Só nesta placa", p.name, value_of(p, ptypes_a[pa[n]]) or p.mfg[:40], pa[n]))
    for n in only_b:
        p = b.parts[pb[n]]
        out.append(("Só na outra", p.name, value_of(p, ptypes_b[pb[n]]) or p.mfg[:40], None))
    # deslocamento médio entre as duas (se a origem mudou)
    dx = dy = 0.0
    if common:
        da = np.array([a.parts[pa[n]].center for n in common])
        db = np.array([b.parts[pb[n]].center for n in common])
        dx, dy = np.median(db - da, axis=0)
    for n in common:
        i, j = pa[n], pb[n]
        p, q = a.parts[i], b.parts[j]
        va, vb = value_of(p, ptypes_a[i]), value_of(q, ptypes_b[j])
        if va and vb:
            xa, xb = part_value(p, ptypes_a[i]), part_value(q, ptypes_b[j])
            changed = _norm(va) != _norm(vb) and (
                (not same_value(xa[0], xb[0])) if (xa and xb) else True)
            if changed:
                out.append(("Valor diferente", p.name, f"{va}  →  {vb}", i))
        elif _norm(p.mfg) != _norm(q.mfg) and p.mfg and q.mfg:
            out.append(("Código diferente", p.name, f"{p.mfg[:40]}  →  {q.mfg[:40]}", i))
        ka, kb = package_of(p), package_of(q)
        if ka and kb and ka != kb:
            out.append(("Encapsulamento diferente", p.name, f"{ka}  →  {kb}", i))
        # ligações: pino -> rede
        na = {a.num[k]: a.nets[a.net[k]] for k in p.pins}
        nb = {b.num[k]: b.nets[b.net[k]] for k in q.pins}
        diffs = [(k, na.get(k), nb.get(k)) for k in sorted(set(na) | set(nb)) if na.get(k) != nb.get(k)]
        if diffs:
            d = ", ".join(f"{k}: {x or '—'} → {y or '—'}" for k, x, y in diffs[:3])
            if len(diffs) > 3:
                d += f" (+{len(diffs) - 3})"
            out.append(("Ligações diferentes", p.name, d, i))
        ca, cb = p.center, q.center
        dist = math.hypot(cb[0] - dx - ca[0], cb[1] - dy - ca[1])
        if dist > max(20.0, 0.5 * max(p.size, q.size)):
            out.append(("Posição diferente", p.name, f"moveu {dist * 0.0254:.1f} mm", i))
    na_ = {n for n in a.nets if n != "UNCONNECTED"}
    nb_ = {n for n in b.nets if n != "UNCONNECTED"}
    for n in sorted(na_ - nb_):
        out.append(("Rede só nesta placa", n, "", None))
    for n in sorted(nb_ - na_):
        out.append(("Rede só na outra", n, "", None))
    return out


CATEGORY_COLORS = {
    "Só nesta placa": (255, 80, 80, 255),
    "Valor diferente": (255, 200, 40, 255),
    "Código diferente": (255, 200, 40, 255),
    "Encapsulamento diferente": (255, 150, 40, 255),
    "Ligações diferentes": (200, 90, 255, 255),
    "Posição diferente": (80, 170, 255, 255),
}


# ------------------------------------------------------------------ datasheet

_RX_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-_.#+]*[A-Za-z0-9]")


def datasheet_query(part, ptype):
    """Texto para pesquisar o datasheet: o código do fabricante quando der para achar no texto do arquivo."""
    mfg = part.mfg or ""
    best = ""
    for tok in _RX_TOKEN.findall(mfg):
        t = tok.strip("._-")
        if len(t) < 5 or not re.search(r"\d", t) or not re.search(r"[A-Za-z]", t):
            continue
        if parse_value(t) is not None or t.upper() in PKG_CODES:
            continue
        if re.match(r"^(XY_)?[A-Z]{1,3}\d+$", t, re.I) and t.upper().endswith(part.name.upper()):
            continue          # é o próprio nome do componente (ex.: XY_R727)
        if len(t) > len(best):
            best = t
    if best:
        return best
    val = value_of(part, ptype)
    kind = {"CapacitorColor": "capacitor", "ResistorColor": "resistor", "InductorColor": "indutor",
            "FuseColor": "fusível"}.get(ptype, "")
    return " ".join(x for x in (kind, val, package_of(part)) if x) or part.name
