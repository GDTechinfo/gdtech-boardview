"""Motor do visualizador de PDF (sem Qt): abre o PDF com o pdfium (pypdfium2), desenha pedaços (ladrilhos)
de página na escala exata da tela e indexa o texto de cada página com a caixa de cada letra.

Tudo que chama o pdfium passa por PDFIUM_LOCK (o pdfium não pode ser usado por duas threads ao mesmo tempo).
A interface nunca chama o pdfium para passar o mouse, selecionar ou procurar: usa o índice (numpy) pronto.

Coordenadas "da página" aqui são sempre em pontos, já giradas como a página aparece na tela, com a origem
no canto de cima/esquerda e y para baixo (u, v)."""
from __future__ import annotations

import atexit
import ctypes
import threading
import weakref

import numpy as np
import pypdfium2 as pdfium
import pypdfium2.raw as pr

PDFIUM_LOCK = threading.RLock()
_WORKERS = weakref.WeakSet()
_EXIT_HOOK = False


def track(thread):
    """Outra thread que usa o pdfium (ex.: índice de peças): também é parada antes de o pypdfium2 fechar tudo ao
    sair. Chame depois de abrir o primeiro documento nela."""
    global _EXIT_HOOK
    _WORKERS.add(thread)
    if not _EXIT_HOOK:
        _EXIT_HOOK = True
        atexit.register(_stop_workers)


def _stop_workers():
    """Ao sair: para as threads antes de o pypdfium2 fechar os documentos (senão o pdfium quebra a memória).
    É registrado depois do primeiro documento aberto, para rodar antes dos finalizadores do pypdfium2."""
    ws = list(_WORKERS)
    for w in ws:
        w.stop()
    for w in ws:
        w.join(3.0)
WORD = r"A-Za-z0-9_"


class PageText:
    """Texto de uma página e a caixa de cada caractere (em pontos da tela, y para baixo)."""
    __slots__ = ("text", "boxes", "valid", "cx", "cy", "_lower")

    def __init__(self, text, boxes):
        self.text = text
        self.boxes = boxes                                    # (n, 4) float32: u0, v0, u1, v1 (ou None)
        self._lower = None
        if boxes is None:                                     # só o texto (primeira passada)
            self.valid = self.cx = self.cy = None
            return
        w = boxes[:, 2] - boxes[:, 0]
        h = boxes[:, 3] - boxes[:, 1]
        self.valid = (w > 0.01) & (h > 0.01) & np.array([not c.isspace() for c in text], dtype=bool) \
            if len(text) else np.zeros(0, dtype=bool)
        self.cx = (boxes[:, 0] + boxes[:, 2]) * 0.5
        self.cy = (boxes[:, 1] + boxes[:, 3]) * 0.5

    def __len__(self):
        return len(self.text)

    @property
    def has_boxes(self):
        return self.boxes is not None

    # -------------------------------------------------------------- mouse
    def char_at(self, u, v, tol=0.0):
        """Índice do caractere sob o ponto (com tolerância em pontos), ou None."""
        if not len(self.text) or self.boxes is None:
            return None
        b = self.boxes
        inside = self.valid & (b[:, 0] - tol <= u) & (u <= b[:, 2] + tol) & (b[:, 1] - tol <= v) & (v <= b[:, 3] + tol)
        idx = np.flatnonzero(inside)
        if idx.size == 0:
            return None
        if idx.size == 1:
            return int(idx[0])
        d = (self.cx[idx] - u) ** 2 + (self.cy[idx] - v) ** 2
        return int(idx[np.argmin(d)])

    def nearest_char(self, u, v):
        """Caractere mais perto do ponto (para continuar a seleção quando o mouse sai do texto)."""
        if self.boxes is None:
            return None
        ok = np.flatnonzero(self.valid)
        if ok.size == 0:
            return None
        b = self.boxes[ok]
        dx = np.maximum(np.maximum(b[:, 0] - u, u - b[:, 2]), 0)
        dy = np.maximum(np.maximum(b[:, 1] - v, v - b[:, 3]), 0)
        return int(ok[np.argmin(dx * dx + dy * dy * 4.0)])

    def word_at(self, i):
        """Início e fim (inclusivo) da palavra que contém o caractere i."""
        t = self.text
        if not (0 <= i < len(t)) or t[i].isspace():
            return i, i
        a = b = i
        while a > 0 and not t[a - 1].isspace():
            a -= 1
        while b + 1 < len(t) and not t[b + 1].isspace():
            b += 1
        return a, b

    def line_at(self, i):
        t = self.text
        a = t.rfind("\n", 0, i) + 1
        b = t.find("\r", i)
        e = t.find("\n", i)
        ends = [x for x in (b, e) if x >= 0]
        b = (min(ends) if ends else len(t)) - 1
        return a, max(a, b)

    def text_range(self, a, b):
        a, b = min(a, b), max(a, b)
        s = self.text[a:b + 1].replace("\r\n", "\n").replace("\r", "\n")
        return s.replace("￾", "").replace("\x02", "-")

    def rects(self, a, b):
        """Retângulos (u0, v0, u1, v1) que cobrem os caracteres a..b, juntando letras da mesma linha."""
        a, b = min(a, b), max(a, b)
        if self.boxes is None:
            return []
        sel = np.arange(a, b + 1)
        sel = sel[self.valid[a:b + 1]]
        out = []
        cur = None
        for i in sel.tolist():
            x0, y0, x1, y1 = self.boxes[i].tolist()
            if cur is not None:
                cx0, cy0, cx1, cy1 = cur
                # mesma linha deitada: sobrepõe na vertical e encosta na horizontal (e o contrário em pé)
                hv = min(cy1 - cy0, y1 - y0)
                hw = min(cx1 - cx0, x1 - x0)
                lying = (min(cy1, y1) - max(cy0, y0)) > 0.5 * hv and max(x0 - cx1, cx0 - x1) < 1.5 * hv
                standing = (min(cx1, x1) - max(cx0, x0)) > 0.5 * hw and max(y0 - cy1, cy0 - y1) < 1.5 * hw
                if lying or standing:
                    cur = (min(cx0, x0), min(cy0, y0), max(cx1, x1), max(cy1, y1))
                    continue
                out.append(cur)
            cur = (x0, y0, x1, y1)
        if cur is not None:
            out.append(cur)
        return out

    # -------------------------------------------------------------- busca
    def find(self, term, whole_word=True):
        """[(início, fim inclusivo)] das ocorrências (sem diferenciar maiúsculas)."""
        if not term or not self.text:
            return []
        if self._lower is None:
            self._lower = self.text.lower()
        t = term.lower()
        hay = self._lower
        out = []
        i = hay.find(t)
        n = len(t)
        while i >= 0:
            if not whole_word or ((i == 0 or not _is_word(hay[i - 1])) and
                                  (i + n >= len(hay) or not _is_word(hay[i + n]))):
                out.append((i, i + n - 1))
            i = hay.find(t, i + 1)
        return out


def _is_word(ch):
    return ch.isalnum() or ch == "_"


_BOX_FN = None


def _char_boxes(tp, n):
    """Caixa (loose) de cada caractere direto na memória: ~2x mais rápido que pela API do pypdfium2."""
    global _BOX_FN
    arr = (pr.FS_RECTF * n)()
    if n == 0:
        return np.zeros((0, 4), dtype=np.float64)
    if _BOX_FN is None:
        addr = ctypes.cast(pr.FPDFText_GetLooseCharBox, ctypes.c_void_p).value
        _BOX_FN = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)(addr)
    fn = _BOX_FN
    h = ctypes.cast(tp.raw, ctypes.c_void_p).value
    base = ctypes.addressof(arr)
    sz = ctypes.sizeof(pr.FS_RECTF)
    for k in range(n):
        fn(h, k, base + k * sz)
    return np.frombuffer(arr, dtype=np.float32).reshape(n, 4).astype(np.float64)


class PdfEngine:
    """Um documento aberto. Os métodos marcados (thread) devem rodar na thread de trabalho."""
    TILE = 512
    RENDER_FLAGS = pr.FPDF_ANNOT

    def __init__(self, path):
        self.path = path
        with PDFIUM_LOCK:
            self.doc = pdfium.PdfDocument(path)
            self.n = len(self.doc)
            self.sizes = [tuple(self.doc.get_page_size(i)) for i in range(self.n)]
        global _EXIT_HOOK
        if not _EXIT_HOOK:
            _EXIT_HOOK = True
            atexit.register(_stop_workers)
        self._pages = {}              # i -> PdfPage (abertas, em ordem de uso)
        self._xf = {}                 # i -> (A, A_inv): página do PDF <-> pontos da tela
        self.closed = False

    # -------------------------------------------------------------- páginas
    def _page(self, i):
        pg = self._pages.pop(i, None)
        if pg is None:
            pg = self.doc[i]
            while len(self._pages) >= 6:              # mantém poucas abertas (memória)
                k = next(iter(self._pages))
                self._pages.pop(k).close()
        self._pages[i] = pg
        return pg

    def _transform(self, i, pg):
        """Afim (pontos da tela u,v) -> (x,y do PDF) e a inversa, tirada do próprio pdfium (vale para
        qualquer giro e caixa de corte)."""
        xf = self._xf.get(i)
        if xf is not None:
            return xf
        w, h = self.sizes[i]
        W, H = max(int(round(w * 100)), 1), max(int(round(h * 100)), 1)
        pts = []
        for dx, dy in ((0, 0), (W, 0), (0, H)):
            px, py = ctypes.c_double(), ctypes.c_double()
            pr.FPDF_DeviceToPage(pg.raw, 0, 0, W, H, 0, dx, dy, ctypes.byref(px), ctypes.byref(py))
            pts.append((px.value, py.value))
        (x0, y0), (x1, y1), (x2, y2) = pts
        # (u, v) em pontos: u = dx/100, v = dy/100
        A = np.array([[(x1 - x0) * 100.0 / W, (x2 - x0) * 100.0 / H, x0],
                      [(y1 - y0) * 100.0 / W, (y2 - y0) * 100.0 / H, y0],
                      [0.0, 0.0, 1.0]])
        xf = (A, np.linalg.inv(A))
        self._xf[i] = xf
        return xf

    # -------------------------------------------------------------- desenho (thread)
    def render(self, i, scale, x0=0, y0=0, w=None, h=None, pad=6):
        """Desenha a região [x0, x0+w) x [y0, y0+h) (pixels) da página i na escala dada (pixels por ponto).
        Desenha 'pad' pixels a mais em volta e corta: sem emenda visível entre ladrilhos.
        Devolve (w, h, stride, bytes BGRx)."""
        pw, ph = self.sizes[i]
        full_w, full_h = max(int(round(pw * scale)), 1), max(int(round(ph * scale)), 1)
        if w is None:
            w, h = full_w, full_h
        if not (0 <= x0 < full_w and 0 <= y0 < full_h):
            return None
        w = max(1, min(w, full_w - x0))
        h = max(1, min(h, full_h - y0))
        # margem só onde há página do lado
        pl, pt_ = min(pad, x0), min(pad, y0)
        pr_, pb = max(0, min(pad, full_w - x0 - w)), max(0, min(pad, full_h - y0 - h))
        bw, bh = w + pl + pr_, h + pt_ + pb
        stride = bw * 4
        buf = (ctypes.c_ubyte * (stride * bh))()
        with PDFIUM_LOCK:
            if self.closed:
                return None
            pg = self._page(i)
            bm = pr.FPDFBitmap_CreateEx(bw, bh, pr.FPDFBitmap_BGRx, buf, stride)
            try:
                pr.FPDFBitmap_FillRect(bm, 0, 0, bw, bh, 0xFFFFFFFF)
                pr.FPDF_RenderPageBitmap(bm, pg.raw, -(x0 - pl), -(y0 - pt_), full_w, full_h, 0, self.RENDER_FLAGS)
            finally:
                pr.FPDFBitmap_Destroy(bm)
        if bw == w and bh == h:
            return w, h, stride, bytes(buf)
        a = np.frombuffer(buf, dtype=np.uint8).reshape(bh, bw * 4)
        return w, h, w * 4, a[pt_:pt_ + h, pl * 4:(pl + w) * 4].tobytes()

    def tile_grid(self, i, scale):
        pw, ph = self.sizes[i]
        T = self.TILE
        return (int(round(pw * scale)) + T - 1) // T, (int(round(ph * scale)) + T - 1) // T

    # -------------------------------------------------------------- texto (thread)
    def index_page(self, i, boxes=True):
        """Texto da página i; com boxes=True também a caixa de cada caractere (bem mais demorado)."""
        with PDFIUM_LOCK:
            if self.closed:
                return None
            pg = self._page(i)
            A, Ainv = self._transform(i, pg)
            tp = pg.get_textpage()
            try:
                n = max(tp.count_chars(), 0)
                text = tp.get_text_range(0, n) if n > 0 else ""
                raw = _char_boxes(tp, n) if boxes else None
            finally:
                tp.close()
        if len(text) != n:                 # caracteres fora do BMP etc.: alinha pelo tamanho
            text = (text + " " * n)[:n]
        if raw is None:
            return PageText(text, None)
        if n:
            # FS_RECTF = (left, top, right, bottom); cantos da caixa -> pontos da tela
            l, t, r, b = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
            xs = np.stack([l, r, l, r], axis=1)
            ys = np.stack([b, b, t, t], axis=1)
            u = Ainv[0, 0] * xs + Ainv[0, 1] * ys + Ainv[0, 2]
            v = Ainv[1, 0] * xs + Ainv[1, 1] * ys + Ainv[1, 2]
            bx = np.stack([u.min(1), v.min(1), u.max(1), v.max(1)], axis=1).astype(np.float32)
            bx[(r <= l) & (t <= b)] = 0
        else:
            bx = np.zeros((0, 4), dtype=np.float32)
        return PageText(text, bx)

    def close(self):
        with PDFIUM_LOCK:
            if self.closed:
                return
            self.closed = True
            for pg in self._pages.values():
                pg.close()
            self._pages.clear()
            self.doc.close()


# ------------------------------------------------------------------ trabalho em segundo plano

class Worker(threading.Thread):
    """Uma thread por documento. Ordem de trabalho:
      1. ladrilhos pedidos pela tela (os mais recentes);
      2. caixas das letras das páginas urgentes (visíveis e onde a busca achou algo);
      3. só o texto de todas as páginas, em ordem (é o que a busca precisa; rápido);
      4. caixas das letras do resto, em segundo plano.

    on_tile(key, (w, h, stride, data)) e on_text(i, PageText) são chamados nesta thread."""

    def __init__(self, engine, on_tile, on_text):
        super().__init__(daemon=True, name="pdf-worker")
        self.eng = engine
        self.on_tile = on_tile
        self.on_text = on_text
        self.cv = threading.Condition()
        self.wanted = []               # [(key, page, scale, x0, y0, w, h)] em ordem de prioridade
        self.urgent = []               # páginas que precisam das caixas já
        self.text_order = list(range(engine.n))
        self.box_order = list(range(engine.n))
        self.has_text = set()
        self.has_boxes = set()
        self.stop_flag = False
        self.busy = None               # chave do ladrilho sendo desenhado agora
        _WORKERS.add(self)

    def want(self, jobs, urgent=None):
        """Troca a fila de desenho (o que não está mais na tela sai da fila) e as páginas urgentes."""
        with self.cv:
            self.wanted = [j for j in jobs if j[0] != self.busy]
            if urgent is not None:
                self.urgent = [p for p in urgent if p not in self.has_boxes]
            self.cv.notify()

    def stop(self):
        with self.cv:
            self.stop_flag = True
            self.wanted = []
            self.cv.notify()

    def _pop(self, order, done):
        while order and order[0] in done:
            order.pop(0)
        return order.pop(0) if order else None

    def _next(self):
        """Próximo trabalho: ("tile", job), ("boxes", página) ou ("text", página); None para sair."""
        with self.cv:
            while True:
                if self.stop_flag:
                    return None
                self.busy = None
                if self.wanted:
                    job = self.wanted.pop(0)
                    self.busy = job[0]
                    return "tile", job
                p = self._pop(self.urgent, self.has_boxes)
                if p is not None:
                    return "boxes", p
                p = self._pop(self.text_order, self.has_text)
                if p is not None:
                    return "text", p
                p = self._pop(self.box_order, self.has_boxes)
                if p is not None:
                    return "boxes", p
                self.cv.wait()

    def run(self):
        try:
            while True:
                nxt = self._next()
                if nxt is None:
                    break
                kind, job = nxt
                if kind == "tile":
                    key, i, scale, x0, y0, w, h = job
                    try:
                        res = self.eng.render(i, scale, x0, y0, w, h)
                    except Exception:  # noqa
                        res = None
                    if res is not None and not self.stop_flag:
                        self.on_tile(key, res)
                    continue
                try:
                    pt = self.eng.index_page(job, boxes=(kind == "boxes"))
                except Exception:  # noqa
                    pt = PageText("", np.zeros((0, 4), dtype=np.float32))
                with self.cv:
                    self.has_text.add(job)
                    if kind == "boxes":
                        self.has_boxes.add(job)
                if pt is not None and not self.stop_flag:
                    self.on_text(job, pt)
        finally:
            self.eng.close()
