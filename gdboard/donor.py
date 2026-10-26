"""Peça em outras placas ("sucata"): acha o mesmo componente em todos os boardviews e esquemas da biblioteca.

Como "o mesmo componente" é decidido:
  - capacitor, resistor, indutor e fusível: mesmo tipo + mesmo valor (+ mesmo encapsulamento, se conhecido).
    Só nos boardviews (todo esquema tem "22uF": procurar valor em PDF não ajuda).
  - o resto (CI, transistor, diodo, conector...): o código do fabricante (ex.: L2N7002SDW1T1G, uP9512R),
    nos boardviews e no texto dos esquemas em PDF. O nome na placa (U7000) não serve: muda de placa para placa.

O índice fica num SQLite (em %APPDATA%) e é incremental: só arquivos novos ou alterados são lidos de novo.
Este módulo não usa Qt (testável sozinho); a janela fica em donorpanel.py."""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time

from . import parsers
from .info import package_of, value_of
from .theme import component_type
from .tools import parse_value, PKG_CODES

INDEX_VERSION = "1"
BOARD_EXTS = {e.lower() for e in parsers.SUPPORTED_EXTS} - {".bom"}
PASSIVE_TYPES = {"CapacitorColor": "Capacitor", "ResistorColor": "Resistor", "InductorColor": "Indutor",
                 "FuseColor": "Fusível"}
_KIND = {"CapacitorColor": "F", "ResistorColor": "R", "InductorColor": "H", "FuseColor": "A"}
_SPLIT = re.compile(r"[\s/,;|]+")
_ALNUM = re.compile(r"[^A-Z0-9]")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT, size INTEGER, mtime REAL,
                                 status TEXT, count INTEGER);
CREATE TABLE IF NOT EXISTS parts(file_id INTEGER, name TEXT, mfg TEXT, norm TEXT, ptype TEXT, val REAL, vkind TEXT,
                                 pkg TEXT, side INTEGER);
CREATE INDEX IF NOT EXISTS parts_file ON parts(file_id);
CREATE INDEX IF NOT EXISTS parts_val ON parts(vkind, val);
CREATE TABLE IF NOT EXISTS pages(file_id INTEGER, page INTEGER, text TEXT);
CREATE INDEX IF NOT EXISTS pages_file ON pages(file_id);
"""


def norm(s):
    """Só letras e números, maiúsculas: 'L2N7002-SDW1T1G' == 'l2n7002sdw1t1g'."""
    return _ALNUM.sub("", (s or "").upper())


class _P:
    """Componente mínimo para as funções de valor/encapsulamento (info.py) sem montar a placa inteira."""
    __slots__ = ("name", "mfg", "footprint")

    def __init__(self, name, mfg, footprint=""):
        self.name, self.mfg, self.footprint = name, mfg or "", footprint or ""


def describe(name, mfg, footprint=""):
    """(tipo, valor numérico ou None, grandeza, encapsulamento) de um componente."""
    ptype = component_type(name)
    p = _P(name, mfg, footprint)
    pkg = package_of(p) or ""
    val = vkind = None
    if ptype in PASSIVE_TYPES:
        txt = value_of(p, ptype)
        pv = parse_value(re.sub(r"\s+", "", txt)) if txt else None
        if pv is not None:
            val, vkind = pv[0], _KIND[ptype]
            if ptype == "ResistorColor" and txt.strip() in ("0", "0R", "0r"):
                val = 0.0
    return ptype, val, vkind, pkg


def code_of(name, mfg):
    """Código do fabricante dentro da descrição do componente ('' se não houver nada que sirva)."""
    mfg = mfg or ""
    best = ""
    for tok in _SPLIT.split(mfg):
        t = tok.strip("._-")
        if len(t) < 4 or not re.search(r"\d", t) or not re.search(r"[A-Za-z]", t):
            continue
        if parse_value(t) is not None or t.upper() in PKG_CODES or re.fullmatch(r"(SOT|SOD|SC|TO|QFN|DFN|SOP|"
                                                                               r"TSSOP|MSOP|SOIC|BGA)[-_]?\d+\w*",
                                                                               t, re.I):
            continue
        if re.fullmatch(r"\d+(\.\d+)?(V|A|W|MA|MHZ|KHZ|PF|NF|UF|OHM|R|%)", t, re.I):
            continue           # 6.3V, 2A, 21PF...
        if norm(t).endswith(norm(name)) and re.fullmatch(r"(XY_)?[A-Z]{1,3}\d+", t, re.I):
            continue           # é o próprio nome do componente
        if len(t) > len(best):
            best = t
    if best:
        return best
    # sem código com número: usa o nome da peça na biblioteca do fabricante (ex.: u_pwr_sw_ul), se parecer um
    first = _SPLIT.split(mfg.strip())[0] if mfg.strip() else ""
    if len(first) >= 5 and "_" in first and parse_value(first) is None:
        return first
    return ""


# ---------------------------------------------------------------------------- consulta

class Query:
    """O que procurar. mode = "codigo" (code) ou "valor" (ptype + val + pkg)."""

    def __init__(self, mode, code="", ptype=None, val=None, pkg="", label="", origin=""):
        self.mode = mode
        self.code = code
        self.ptype = ptype
        self.val = val
        self.pkg = pkg
        self.label = label             # ex.: "U7000 — uP9512R QFN-40"
        self.origin = origin           # arquivo de onde veio (fica fora dos resultados)

    def describe(self):
        if self.mode == "valor":
            kind = PASSIVE_TYPES.get(self.ptype, "Componente")
            return f"{kind} {fmt_value(self.val, self.ptype)}" + (f" · {self.pkg}" if self.pkg else "")
        return f"código “{self.code}”"

    def ok(self):
        if self.mode == "valor":
            return self.val is not None and self.ptype in PASSIVE_TYPES
        return len(norm(self.code)) >= 3


def query_for_part(name, mfg, footprint="", origin=""):
    ptype, val, vkind, pkg = describe(name, mfg, footprint)
    label = f"{name} — {mfg}" if mfg else name
    if val is not None:
        return Query("valor", ptype=ptype, val=val, pkg=pkg, label=label, origin=origin)
    return Query("codigo", code=code_of(name, mfg), label=label, origin=origin)


def fmt_value(v, ptype):
    if v is None:
        return "?"
    unit = {"CapacitorColor": "F", "ResistorColor": "Ω", "InductorColor": "H", "FuseColor": "A"}.get(ptype, "")
    if v == 0:
        return f"0{unit}"
    for mult, pre in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"),
                      (1e-12, "p")):
        if abs(v) >= mult * 0.9999:
            x = v / mult
            s = f"{x:.3g}"
            return f"{s}{pre}{unit}"
    return f"{v:g}{unit}"


# ---------------------------------------------------------------------------- banco

def connect(db_path):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    con = sqlite3.connect(db_path, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    v = con.execute("SELECT v FROM meta WHERE k='version'").fetchone()
    if v is None or v[0] != INDEX_VERSION:
        con.executescript("DELETE FROM parts; DELETE FROM pages; DELETE FROM files;")
        con.execute("INSERT OR REPLACE INTO meta VALUES('version', ?)", (INDEX_VERSION,))
        con.commit()
    return con


def stats(con):
    r = dict(con.execute("SELECT kind, COUNT(*) FROM files WHERE status='ok' GROUP BY kind").fetchall())
    return r.get("board", 0), r.get("pdf", 0)


def walk(roots):
    out = []
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                ext = os.path.splitext(fn)[1].lower()
                kind = "board" if ext in BOARD_EXTS else "pdf" if ext == ".pdf" else None
                if kind is None:
                    continue
                p = os.path.abspath(os.path.join(dirpath, fn))
                if p in seen:
                    continue
                seen.add(p)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, kind, st.st_size, st.st_mtime))
    return out


class Indexer(threading.Thread):
    """Atualiza o índice em segundo plano. progress(feitos, total, arquivo) e done(placas, pdfs) são chamados
    nesta thread (a janela repassa para a interface por sinal)."""

    def __init__(self, db_path, roots, keys=None, progress=None, done=None, should_stop=None):
        super().__init__(daemon=True, name="indice-pecas")
        self.db_path = db_path
        self.roots = list(roots)
        self.keys = keys or {}
        self.progress = progress or (lambda *a: None)
        self.done = done or (lambda *a: None)
        self._stop = False
        self._should_stop = should_stop or (lambda: False)

    @property
    def stop_flag(self):
        return self._stop or self._should_stop()

    def stop(self):
        self._stop = True

    def run(self):
        con = connect(self.db_path)
        try:
            files = walk(self.roots)
            have = {p: (i, s, m) for i, p, s, m in con.execute("SELECT id, path, size, mtime FROM files")}
            present = {f[0] for f in files}
            gone = [i for p, (i, _s, _m) in have.items() if p not in present]
            for i in gone:
                self._drop(con, i)
            con.commit()
            todo = [f for f in files if f[0] not in have or have[f[0]][1] != f[2] or abs(have[f[0]][2] - f[3]) > 1e-3]
            todo.sort(key=lambda f: (f[1] != "board", f[0].lower()))       # placas primeiro (mais rápido)
            for k, (p, kind, size, mtime) in enumerate(todo):
                if self.stop_flag:
                    break
                self.progress(k, len(todo), p)
                old = have.get(p)
                if old is not None:
                    self._drop(con, old[0])
                cur = con.execute("INSERT INTO files(path, kind, size, mtime, status, count) VALUES(?,?,?,?,?,0)",
                                  (p, kind, size, mtime, "lendo"))
                fid = cur.lastrowid
                try:
                    if kind == "board":
                        status, count = self._board(con, fid, p)
                    else:
                        status, count = self._pdf(con, fid, p)
                except Exception as e:  # noqa — arquivo ruim não para o índice
                    status, count = "erro: " + str(e)[:120], 0
                con.execute("UPDATE files SET status=?, count=? WHERE id=?", (status, count, fid))
                con.commit()
                time.sleep(0.001)
            self.progress(len(todo), len(todo), "")
        finally:
            b, p = stats(con)
            con.close()
            self.done(b, p)

    @staticmethod
    def _drop(con, fid):
        con.execute("DELETE FROM parts WHERE file_id=?", (fid,))
        con.execute("DELETE FROM pages WHERE file_id=?", (fid,))
        con.execute("DELETE FROM files WHERE id=?", (fid,))

    def _board(self, con, fid, path):
        try:
            raw = parsers.load_file(path, self.keys)
        except parsers.KeyRequired:
            return "chave", 0
        rows = []
        for rp in raw.parts:
            if rp.name.startswith("..."):
                continue
            mfg = rp.mfg or ""
            fp = getattr(rp, "footprint", "") or ""
            ptype, val, vkind, pkg = describe(rp.name, mfg, fp)
            rows.append((fid, rp.name, mfg, norm(mfg + " " + fp) if (mfg or fp) else "", ptype or "", val, vkind,
                         pkg, int(rp.side)))
        con.executemany("INSERT INTO parts VALUES(?,?,?,?,?,?,?,?,?)", rows)
        return "ok", len(rows)

    def _pdf(self, con, fid, path):
        import pypdfium2 as pdfium
        from .pdfengine import PDFIUM_LOCK
        from .pdfengine import track
        with PDFIUM_LOCK:
            doc = pdfium.PdfDocument(path)
            n = len(doc)
        track(self)
        total = 0
        try:
            for i in range(n):
                if self.stop_flag:
                    break
                with PDFIUM_LOCK:
                    pg = doc[i]
                    tp = pg.get_textpage()
                    try:
                        t = tp.get_text_range()
                    finally:
                        tp.close()
                        pg.close()
                t = t.replace("\r\n", "\n").replace("\r", "\n")
                if t.strip():
                    con.execute("INSERT INTO pages VALUES(?,?,?)", (fid, i, t))
                    total += len(t)
                time.sleep(0.0005)       # deixa a janela respirar (o pdfium é compartilhado com o visualizador)
        finally:
            with PDFIUM_LOCK:
                doc.close()
        return ("ok" if total >= 30 else "sem texto"), n


# ---------------------------------------------------------------------------- busca

class Hit:
    __slots__ = ("path", "kind", "items", "score")

    def __init__(self, path, kind):
        self.path, self.kind, self.items, self.score = path, kind, [], 0


def _code_score(code_n, mfg):
    """3 = mesmo código; 2 = código + sufixo (variante/embalagem); 1 = contém (ex.: L2N7002 x 2N7002)."""
    best = 0
    for tok in _SPLIT.split(mfg or ""):
        t = norm(tok)
        if not t or code_n not in t:
            continue
        best = max(best, 3 if t == code_n else 2 if t.startswith(code_n) else 1)
    return best or 1


def search(con, q: Query, boards=True, pdfs=True, limit_files=400, limit_items=300, exclude=()):
    """Devolve [Hit] (placas e PDFs), os melhores primeiro."""
    excl = {os.path.abspath(p) for p in exclude if p}
    hits: dict[str, Hit] = {}
    if q.mode == "valor":
        if boards and q.val is not None:
            lo, hi = (q.val - abs(q.val) * 1e-3, q.val + abs(q.val) * 1e-3) if q.val else (-1e-15, 1e-15)
            sql = ("SELECT f.path, p.name, p.mfg, p.side, p.pkg FROM parts p JOIN files f ON f.id = p.file_id "
                   "WHERE p.vkind = ? AND p.val BETWEEN ? AND ? AND p.ptype = ?")
            args = [_KIND[q.ptype], lo, hi, q.ptype]
            if q.pkg:
                sql += " AND p.pkg = ?"
                args.append(q.pkg)
            for path, name, mfg, side, pkg in con.execute(sql, args):
                if path in excl:
                    continue
                h = hits.setdefault(path, Hit(path, "board"))
                if len(h.items) < limit_items:
                    h.items.append((name, mfg, side, ""))
                h.score += 1
        return _rank(hits, limit_files)

    code_n = norm(q.code)
    if len(code_n) < 3:
        return []
    if boards:
        for path, name, mfg, side in con.execute(
                "SELECT f.path, p.name, p.mfg, p.side FROM parts p JOIN files f ON f.id = p.file_id "
                "WHERE p.norm LIKE ?", ("%" + code_n + "%",)):
            if path in excl:
                continue
            h = hits.setdefault(path, Hit(path, "board"))
            sc = _code_score(code_n, mfg)
            if len(h.items) < limit_items:
                h.items.append((name, mfg, side, sc))
            h.score = max(h.score, sc * 1000) + 1
    if pdfs:
        term = q.code.strip()
        rx = re.compile(re.escape(term).replace(r"\ ", r"\s*").replace(r"\-", r"[-_\s]?"), re.I)
        for path, page, text in con.execute(
                "SELECT f.path, g.page, g.text FROM pages g JOIN files f ON f.id = g.file_id "
                "WHERE g.text LIKE ?", ("%" + term + "%",)):
            if path in excl:
                continue
            snips = []
            for m in rx.finditer(text):
                a, b = max(0, m.start() - 40), min(len(text), m.end() + 40)
                left = text[a:m.start()].replace("\n", " ")
                right = text[m.end():b].replace("\n", " ")
                snips.append((left, text[m.start():m.end()], right))
                if len(snips) >= 3:
                    break
            if not snips:
                continue
            h = hits.setdefault(path, Hit(path, "pdf"))
            if len(h.items) < limit_items:
                h.items.append((page, snips, term))
            h.score += 2
    return _rank(hits, limit_files)


def _rank(hits, limit):
    out = sorted(hits.values(), key=lambda h: (-h.score, h.kind != "board", os.path.basename(h.path).lower()))
    for h in out:
        if h.kind == "board":
            h.items.sort(key=lambda it: (-(it[3] or 0) if isinstance(it[3], int) else 0, _natkey(it[0])))
        else:
            h.items.sort(key=lambda it: it[0])
    return out[:limit]


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]
