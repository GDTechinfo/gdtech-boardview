"""Biblioteca de boardviews: indexa as pastas escolhidas (em segundo plano), acha o PDF do esquema de cada
placa e gera miniaturas dos arquivos recentes."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

from . import parsers

BOARD_EXTS = {e.lower() for e in parsers.SUPPORTED_EXTS}
_TOK = re.compile(r"[a-z0-9]+")


def tokens(name):
    """Pedaços do nome que ajudam a casar placa e esquema (ex.: 'MS-V389 Rev5.0' -> ms, v389, rev5, 0)."""
    stem = os.path.splitext(os.path.basename(name))[0].lower()
    return {t for t in _TOK.findall(stem) if len(t) >= 2 and t not in ("boardview", "board", "view", "schematic",
                                                                         "schematics", "esquema", "cad", "pdf", "rev")}


def pdf_score(board_path, pdf_path):
    a, b = tokens(board_path), tokens(pdf_path)
    if not a or not b:
        return 0.0
    common = a & b
    # números de modelo (com dígito) valem mais
    # números de modelo (com dígito) valem mais; precisa de pelo menos um em comum para casar (nota >= 3)
    return sum(3.0 if any(ch.isdigit() for ch in t) else 1.0 for t in common) - 0.01 * len(b - a)


def find_pdf_for(board_path):
    """PDF do esquema para uma placa: na mesma pasta (ou na de cima). Um PDF sozinho na pasta é ele;
    com vários, escolhe o de nome mais parecido."""
    folder = os.path.dirname(os.path.abspath(board_path))
    for d in (folder, os.path.dirname(folder)):
        try:
            pdfs = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(".pdf")]
        except OSError:
            continue
        if not pdfs:
            continue
        if len(pdfs) == 1 and d == folder:
            return pdfs[0]
        best = max(pdfs, key=lambda p: pdf_score(board_path, p))
        if pdf_score(board_path, best) >= 2.9:
            return best
    return None


def pair_pdfs(boards, pdfs):
    """Arquivos soltos juntos na janela: casa cada placa com um PDF (pelo nome; um par só = os dois)."""
    out = {}
    if len(boards) == 1 and len(pdfs) >= 1:
        out[os.path.abspath(boards[0])] = max(pdfs, key=lambda p: pdf_score(boards[0], p))
        return out
    for bd in boards:
        if pdfs:
            best = max(pdfs, key=lambda p: pdf_score(bd, p))
            if pdf_score(bd, best) > 0:
                out[os.path.abspath(bd)] = best
    return out


# ------------------------------------------------------------------ índice

class LibraryIndex:
    def __init__(self, cache_file):
        self.cache_file = cache_file
        self.items: list[dict] = []        # {"path", "name", "ext", "size", "mtime", "root", "pdf"}
        self.when = 0.0
        self.roots: list[str] = []
        self.load()

    def load(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.items = d.get("items", [])
            self.when = d.get("when", 0.0)
            self.roots = d.get("roots", [])
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"when": self.when, "roots": self.roots, "items": self.items}, f, ensure_ascii=False)
            os.replace(tmp, self.cache_file)
        except OSError:
            pass

    @staticmethod
    def scan(roots, stop=lambda: False):
        """Varre as pastas. Devolve a lista de itens (pode demorar: rode fora da interface)."""
        items = []
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                if stop():
                    return items
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                pdfs = [f for f in filenames if f.lower().endswith(".pdf")]
                for fn in filenames:
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in BOARD_EXTS:
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    pdf = ""
                    if len(pdfs) == 1:
                        pdf = os.path.join(dirpath, pdfs[0])
                    elif pdfs:
                        best = max(pdfs, key=lambda q: pdf_score(fn, q))
                        if pdf_score(fn, best) >= 2.9:
                            pdf = os.path.join(dirpath, best)
                    items.append({"path": p, "name": fn, "ext": ext, "size": st.st_size, "mtime": st.st_mtime,
                                  "root": root, "pdf": pdf})
        return items

    def search(self, query, limit=500):
        toks = [t for t in query.lower().split() if t]
        out = []
        for it in self.items:
            hay = it["path"].lower()
            if all(t in hay for t in toks):
                out.append(it)
                if len(out) >= limit:
                    break
        return out

    def stats(self):
        n = len(self.items)
        with_pdf = sum(1 for it in self.items if it.get("pdf"))
        return n, with_pdf


# ------------------------------------------------------------------ miniaturas

def thumb_path(base_dir, board_path):
    key = hashlib.sha1(os.path.abspath(board_path).lower().encode("utf-8")).hexdigest()[:20]
    return os.path.join(base_dir, "miniaturas", key + ".png")


def make_thumbnail(board, theme, out_file, W=320, H=200):
    """Desenha uma miniatura da placa (lado de cima) e salva em PNG."""
    import numpy as np
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage, QPainter, QColor, QPen, QPolygonF
    from .parsers import TOP, BOTH
    x0, y0, x1, y1 = board.bounds
    w, h = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    pad = 10
    k = min((W - 2 * pad) / w, (H - 2 * pad) / h)
    ox = (W - w * k) / 2
    oy = (H - h * k) / 2
    img = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    bg = theme.get("Background")
    img.fill(QColor(bg[0], bg[1], bg[2]))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    tx = lambda x: ox + (x - x0) * k
    ty = lambda y: H - (oy + (y - y0) * k)        # y da placa cresce para cima
    oc = theme.get("BoardOutline")
    p.setPen(QPen(QColor(oc[0], oc[1], oc[2]), 1))
    for a, b in board.outline_segs[:20000]:
        p.drawLine(QPointF(tx(a[0]), ty(a[1])), QPointF(tx(b[0]), ty(b[1])))
    vis = (board.side == TOP) | (board.side == BOTH)
    xs, ys = tx(board.pad_x[vis]), ty(board.pad_y[vis])
    gnd = board.net_is_gnd[board.net[vis]]
    for mask, key in ((~gnd, "PinsPads"), (gnd, "GndColor")):
        c = theme.get(key, "PinsPads")
        if mask.any():
            p.setPen(QPen(QColor(c[0], c[1], c[2], 200), 1.3))
            p.drawPoints(QPolygonF([QPointF(a, b) for a, b in zip(xs[mask].tolist(), ys[mask].tolist())]))
    p.end()
    try:
        os.makedirs(os.path.dirname(out_file), exist_ok=True)
        img.save(out_file, "PNG")
    except OSError:
        pass


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


def fmt_age(t):
    d = time.time() - t
    if d < 3600:
        return "agora há pouco"
    if d < 86400:
        return f"há {int(d // 3600)} h"
    if d < 86400 * 60:
        return f"há {int(d // 86400)} dias"
    return time.strftime("%d/%m/%Y", time.localtime(t))
