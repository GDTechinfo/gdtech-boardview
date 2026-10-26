"""Visualizador do esquema elétrico (PDF) com o pdfium.

- Nitidez: cada pedaço da página é desenhado na escala exata da tela (inclusive em telas com escala do
  Windows de 125 %/150 %). Durante o zoom aparece a imagem anterior esticada e, assim que o zoom para,
  os pedaços nítidos chegam.
- Arrastar em cima de texto seleciona (Ctrl+C copia); arrastar fora do texto move a página.
  Duplo clique seleciona a palavra, triplo clique a linha. Botão do meio sempre move.
- A busca usa a caixa de cada letra: o componente achado fica no centro da tela, destacado.
- O desenho e a leitura do texto rodam em outra thread: a janela nunca trava."""
from __future__ import annotations

import bisect
import math
import os
import time
from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, QPointF, QRectF, Signal, QObject, QSettings
from PySide6.QtGui import QImage, QPainter, QColor, QPen, QBrush, QKeySequence, QAction, QGuiApplication, QClipboard
from PySide6.QtWidgets import (QAbstractScrollArea, QMainWindow, QToolBar, QLineEdit, QLabel, QFileDialog,
                               QMessageBox, QWidget, QSizePolicy, QComboBox, QSpinBox, QMenu, QApplication, QFrame)

from .pdfengine import PdfEngine, Worker

GAP = 12                       # entre páginas (pixels)
MARGIN = 16
CACHE_BYTES = 320 * 1024 * 1024
PREVIEW_PX = 1100              # tamanho da miniatura de cada página (mostrada enquanto o resto chega)
SETTLE_MS = 140
ZOOMS = (25, 50, 75, 100, 125, 150, 200, 300, 400, 600, 800, 1200, 1600)

BG = QColor(0x2a, 0x2e, 0x35)
SEL_FILL = QColor(51, 153, 255, 85)
SEL_LINE = QColor(51, 153, 255, 160)
HIT_FILL = QColor(255, 220, 0, 70)
CUR_FILL = QColor(255, 140, 0, 110)
CUR_LINE = QColor(255, 120, 0)
PIN_FILL = QColor(0, 200, 255, 90)       # pino achado (duplo clique num pino da placa)
PIN_LINE = QColor(0, 160, 255)


class _Bridge(QObject):
    tile = Signal(int, object, object)          # geração, chave, QImage
    text = Signal(int, int, object)             # geração, página, PageText


class PdfCanvas(QAbstractScrollArea):
    zoomChanged = Signal()
    pageChanged = Signal(int)
    selectionChanged = Signal(str)
    searchChanged = Signal()
    lookupRequested = Signal(str)               # "mostrar na placa"
    donorRequested = Signal(str)                # "procurar em outras placas e esquemas"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)
        vp = self.viewport()
        vp.setMouseTracking(True)
        vp.setAttribute(Qt.WA_OpaquePaintEvent, True)
        vp.setCursor(Qt.OpenHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.eng = None
        self.worker = None
        self.gen = 0
        self.sizes = []
        self.texts = {}
        self.zoom = 1.0
        self.fit_mode = "width"
        self.req_scale = None
        self.invert = False
        self.cache = OrderedDict()               # chave -> QImage
        self.cache_bytes = 0
        self.scales = {}                         # página -> {escala: nº de ladrilhos no cache}
        self.page_x, self.page_y, self.page_w, self.page_h = [], [], [], []
        self.doc_w = self.doc_h = 0.0
        self.cur_page = -1
        # seleção
        self.sel = None                          # (página, âncora, ponta)
        self._mode = None                        # "select" | "pan"
        self._press = None
        self._last_dbl = 0.0
        # busca
        self.term = ""
        self.whole = True
        self.hits = {}                           # página -> [(a, b)]
        self.order = []                          # [(página, a, b)] na ordem de leitura
        self.cur_hit = None
        self.search_done = True
        self._jump_pending = False
        self._pending_goto = None                # resultado esperando as caixas das letras da página
        self._flash_t0 = None
        # duplo clique num pino da placa: (número do pino, rede) — a busca acha o componente e depois o pino
        self.pin_target = None
        self.pin_mark = None                     # (página, [retângulos]) do pino achado
        self.pin_status = ""
        self._pin_first = False
        self._pin_auto = False
        self.pin_count = 99

        self.bridge = _Bridge()
        self.bridge.tile.connect(self._tile_ready, Qt.QueuedConnection)
        self.bridge.text.connect(self._text_ready, Qt.QueuedConnection)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(SETTLE_MS)
        self._settle.timeout.connect(self._zoom_settled)
        self._req_timer = QTimer(self)
        self._req_timer.setSingleShot(True)
        self._req_timer.setInterval(0)
        self._req_timer.timeout.connect(self._request)
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(16)
        self._flash_timer.timeout.connect(self._flash_step)
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(self._scrolled)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(40)

    # ------------------------------------------------------------------ documento
    def open(self, path):
        eng = PdfEngine(path)                    # pode dar erro (arquivo ruim, com senha...)
        self.close_doc()
        self.gen += 1
        g = self.gen
        bridge = self.bridge

        def on_tile(key, res, g=g):
            w, h, stride, data = res
            img = QImage(data, w, h, stride, QImage.Format_RGB32).copy()
            if key[-1]:                          # chave pedida com cores invertidas
                img.invertPixels()
            bridge.tile.emit(g, key, img)

        def on_text(i, pt, g=g):
            bridge.text.emit(g, i, pt)

        self.eng = eng
        self.sizes = list(eng.sizes)
        self.worker = Worker(eng, on_tile, on_text)
        self.worker.start()
        self.fit_mode = "width"
        self.zoom = self._fit_zoom("width")
        self.req_scale = None
        self._relayout()
        self.verticalScrollBar().setValue(0)
        self.horizontalScrollBar().setValue(0)
        self._zoom_settled()
        self.zoomChanged.emit()
        self._scrolled()
        return True

    def close_doc(self):
        if self.worker is not None:
            self.worker.stop()                   # a thread fecha o documento quando terminar o que está fazendo
        self.worker = None
        self.eng = None
        self.gen += 1
        self.sizes = []
        self.texts = {}
        self._clear_cache()
        self.sel = None
        self.clear_search()
        self.cur_page = -1
        self._relayout()
        self.viewport().update()

    def page_count(self):
        return len(self.sizes)

    def trim_cache(self):
        """Janela fechada (só escondida): solta os ladrilhos e fica só com as miniaturas das páginas."""
        for k in [k for k in self.cache if k[0] == "t"]:
            self.cache_bytes -= self.cache.pop(k).sizeInBytes()
        self.scales = {}

    def _clear_cache(self):
        self.cache.clear()
        self.cache_bytes = 0
        self.scales = {}

    # ------------------------------------------------------------------ geometria
    def _dpr(self):
        return self.viewport().devicePixelRatioF()

    def _pt_px(self):
        """Pixels por ponto em 100 % (1 polegada da folha = 1 polegada da tela)."""
        return self.logicalDpiX() / 72.0

    def _fit_zoom(self, mode):
        if not self.sizes:
            return 1.0
        vw = max(self.viewport().width() - 2 * MARGIN, 50)
        vh = max(self.viewport().height() - 2 * MARGIN, 50)
        i = self.cur_page if 0 <= self.cur_page < len(self.sizes) else 0
        w, h = self.sizes[i]
        if mode == "page":
            return min(vw / w, vh / h)
        return vw / max(s[0] for s in self.sizes)

    def _relayout(self):
        z = self.zoom
        self.page_w = [w * z for w, _ in self.sizes]
        self.page_h = [h * z for _, h in self.sizes]
        self.doc_w = (max(self.page_w) if self.page_w else 0) + 2 * MARGIN
        y = MARGIN
        self.page_y = []
        for h in self.page_h:
            self.page_y.append(y)
            y += h + GAP
        self.doc_h = (y - GAP + MARGIN) if self.page_h else 0
        self.page_x = [(self.doc_w - w) / 2 for w in self.page_w]
        self._update_bars()

    def _update_bars(self):
        vp = self.viewport()
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setRange(0, max(0, int(math.ceil(self.doc_w - vp.width()))))
        v.setRange(0, max(0, int(math.ceil(self.doc_h - vp.height()))))
        h.setPageStep(vp.width())
        v.setPageStep(max(vp.height() - 40, 40))

    def _origin(self):
        vp = self.viewport()
        ox = self.horizontalScrollBar().value() - max(0.0, (vp.width() - self.doc_w) / 2)
        oy = self.verticalScrollBar().value() - max(0.0, (vp.height() - self.doc_h) / 2)
        return ox, oy

    def _pages_between(self, y0, y1):
        if not self.page_y:
            return range(0)
        a = max(0, bisect.bisect_right(self.page_y, y0) - 1)
        b = bisect.bisect_right(self.page_y, y1)
        return range(a, min(b, len(self.page_y)))

    def hit_page(self, pos, clamp=False):
        """Ponto da janela -> (página, u, v em pontos, dentro?)."""
        if not self.page_y:
            return None
        ox, oy = self._origin()
        x, y = pos.x() + ox, pos.y() + oy
        i = max(0, bisect.bisect_right(self.page_y, y) - 1)
        if clamp and i + 1 < len(self.page_y) and y > self.page_y[i] + self.page_h[i] + GAP / 2:
            i += 1
        u = (x - self.page_x[i]) / self.zoom
        v = (y - self.page_y[i]) / self.zoom
        w, h = self.sizes[i]
        inside = 0 <= u <= w and 0 <= v <= h
        return i, u, v, inside

    def page_to_view(self, i, u, v):
        ox, oy = self._origin()
        return QPointF(self.page_x[i] + u * self.zoom - ox, self.page_y[i] + v * self.zoom - oy)

    def _rect_to_view(self, i, r):
        p = self.page_to_view(i, r[0], r[1])
        return QRectF(p.x(), p.y(), (r[2] - r[0]) * self.zoom, (r[3] - r[1]) * self.zoom)

    # ------------------------------------------------------------------ zoom / rolagem
    def set_zoom(self, z, anchor=None, fit=None, settle_now=False):
        if not self.sizes:
            return
        z = max(0.03, min(float(z), 60.0))
        vp = self.viewport()
        if anchor is None:
            anchor = QPointF(vp.width() / 2, vp.height() / 2)
        hp = self.hit_page(anchor, clamp=True)
        self.fit_mode = fit
        if abs(z - self.zoom) > 1e-9:
            self.zoom = z
            self._relayout()
            if hp is not None:
                i, u, v, _ = hp
                self.center_on(i, u, v, at=anchor)
            self.zoomChanged.emit()
        if settle_now:
            self._zoom_settled()
        else:
            self._settle.start()
        self.viewport().update()

    def zoom_by(self, f, anchor=None):
        self.set_zoom(self.zoom * f, anchor)

    def zoom_percent(self):
        return 100.0 * self.zoom / self._pt_px()

    def set_zoom_percent(self, pct):
        self.set_zoom(pct / 100.0 * self._pt_px(), settle_now=True)

    def fit(self, mode):
        self.set_zoom(self._fit_zoom(mode), fit=mode, settle_now=True)
        if mode == "page" and 0 <= self.cur_page < len(self.page_y):
            self.go_page(self.cur_page)

    def center_on(self, i, u, v, at=None):
        """Põe o ponto (u, v) da página i no ponto 'at' da janela (no centro, se não disser)."""
        vp = self.viewport()
        if at is None:
            at = QPointF(vp.width() / 2, vp.height() / 2)
        dx = self.page_x[i] + u * self.zoom
        dy = self.page_y[i] + v * self.zoom
        self.horizontalScrollBar().setValue(int(round(dx - at.x() + max(0.0, (vp.width() - self.doc_w) / 2))))
        self.verticalScrollBar().setValue(int(round(dy - at.y() + max(0.0, (vp.height() - self.doc_h) / 2))))

    def go_page(self, i):
        if 0 <= i < len(self.page_y):
            self.verticalScrollBar().setValue(int(self.page_y[i] - MARGIN / 2))

    def _scrolled(self, *_):
        if self.page_y:
            vp = self.viewport()
            ox, oy = self._origin()
            yc = oy + vp.height() / 3
            i = max(0, min(bisect.bisect_right(self.page_y, yc) - 1, len(self.page_y) - 1))
            if i != self.cur_page:
                self.cur_page = i
                self.pageChanged.emit(i)
        self._req_timer.start()
        self.viewport().update()

    def _zoom_settled(self):
        self.req_scale = self.zoom * self._dpr()
        self._req_timer.start()
        self.viewport().update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit_mode and self.sizes:
            top = self.hit_page(QPointF(self.viewport().width() / 2, 0), clamp=True)
            self.zoom = self._fit_zoom(self.fit_mode)
            self._relayout()
            if top is not None:
                self.center_on(top[0], top[1], top[2], at=QPointF(self.viewport().width() / 2, 0))
            self._settle.start()
            self.zoomChanged.emit()
        else:
            self._update_bars()
        self._req_timer.start()

    # ------------------------------------------------------------------ pedidos de desenho
    def _tile_range(self, i, scale, X, Y, x0, y0, x1, y1):
        """Ladrilhos (escala 'scale') da página i que cobrem a área [x0,x1]x[y0,y1] da janela (pixels de tela).
        X, Y: canto da página na janela, em pixels de tela."""
        T = PdfEngine.TILE
        nx, ny = self.eng.tile_grid(i, scale) if self.eng else (0, 0)
        tx0 = max(0, int((x0 - X) // T))
        ty0 = max(0, int((y0 - Y) // T))
        tx1 = min(nx - 1, int((x1 - X) // T))
        ty1 = min(ny - 1, int((y1 - Y) // T))
        return tx0, ty0, tx1, ty1

    def _request(self):
        if self.eng is None or self.worker is None:
            return
        dpr = self._dpr()
        s = self.req_scale or self.zoom * dpr
        sk = round(s, 4)
        vp = self.viewport()
        vw, vh = vp.width() * dpr, vp.height() * dpr
        ox, oy = self._origin()
        ext = vh * 0.6
        first, near, prev_jobs, far = [], [], [], []
        vis = list(self._pages_between(oy - ext / dpr, oy + (vh + ext) / dpr))
        T = PdfEngine.TILE
        inv = self.invert
        f = sk / (self.zoom * dpr)                # ainda assentando o zoom: pede na escala antiga
        W, H = vw * f, vh * f
        E = ext * f
        for i in vis:
            pw, ph = self.sizes[i]
            # canto da página na janela, em pixels da escala pedida
            X = round((self.page_x[i] - ox) * dpr) * f
            Y = round((self.page_y[i] - oy) * dpr) * f
            on_screen = (self.page_y[i] - oy) * dpr < vh and (self.page_y[i] + self.page_h[i] - oy) * dpr > 0
            pk = ("p", i, inv)
            if pk not in self.cache:
                job = (pk, i, PREVIEW_PX / max(pw, ph), 0, 0, None, None)
                (first if (on_screen and not self.scales.get(i)) else prev_jobs).append(job)
            tx0, ty0, tx1, ty1 = self._tile_range(i, sk, X, Y, 0, -E, W, H + E)
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    key = ("t", i, sk, tx, ty, inv)
                    if key in self.cache:
                        continue
                    mx = X + tx * T + T / 2
                    my = Y + ty * T + T / 2
                    vis_tile = (Y + ty * T < H) and (Y + (ty + 1) * T > 0)
                    d = (mx - W / 2) ** 2 + (my - H / 2) ** 2
                    job = (key, i, sk, tx * T, ty * T, T, T)
                    (near if vis_tile else far).append((d, job))
        near.sort(key=lambda t: t[0])
        far.sort(key=lambda t: t[0])
        jobs = first + [j for _, j in near] + prev_jobs + [j for _, j in far]
        # vizinhas: miniaturas das próximas páginas (rolar fica instantâneo)
        if vis:
            for i in (vis[-1] + 1, vis[-1] + 2, vis[0] - 1):
                if 0 <= i < len(self.sizes):
                    pk = ("p", i, inv)
                    if pk not in self.cache:
                        jobs.append((pk, i, PREVIEW_PX / max(self.sizes[i]), 0, 0, None, None))
        urgent = [i for i in vis if (self.page_y[i] - oy) < vp.height() and
                  (self.page_y[i] + self.page_h[i] - oy) > 0]
        if self._pending_goto is not None:
            urgent.insert(0, self._pending_goto[0])
        self.worker.want(jobs, urgent)

    def _tile_ready(self, g, key, img):
        if g != self.gen:
            return
        if key[-1] != self.invert or key in self.cache:
            return
        self.cache[key] = img
        self.cache_bytes += img.sizeInBytes()
        if key[0] == "t":
            d = self.scales.setdefault(key[1], {})
            d[key[2]] = d.get(key[2], 0) + 1
        while self.cache_bytes > CACHE_BYTES and len(self.cache) > 1:
            k, old = self.cache.popitem(last=False)
            self.cache_bytes -= old.sizeInBytes()
            if k[0] == "t":
                d = self.scales.get(k[1], {})
                d[k[2]] = d.get(k[2], 1) - 1
                if d[k[2]] <= 0:
                    d.pop(k[2], None)
        self.viewport().update()

    # ------------------------------------------------------------------ desenho
    def paintEvent(self, e):
        p = QPainter(self.viewport())
        p.fillRect(e.rect(), BG)
        if self.eng is None:
            p.setPen(QColor(150, 156, 168))
            p.drawText(self.viewport().rect(), Qt.AlignCenter,
                       "Nenhum PDF aberto.\nUse “Abrir PDF…” ou arraste o esquema para cá.")
            p.end()
            return
        dpr = self._dpr()
        vp = self.viewport()
        vw, vh = vp.width(), vp.height()
        ox, oy = self._origin()
        s_now = self.zoom * dpr
        sk = round(self.req_scale or s_now, 4)
        if abs(sk - s_now) > 1e-3 and not self._settle.isActive():
            self._settle.start()            # mudou a escala da tela (outro monitor): redesenha nítido
        page_bg = QColor(0, 0, 0) if self.invert else QColor(255, 255, 255)
        for i in self._pages_between(oy, oy + vh):
            X = round((self.page_x[i] - ox) * dpr)
            Y = round((self.page_y[i] - oy) * dpr)
            r = QRectF(X / dpr, Y / dpr, self.page_w[i], self.page_h[i])
            p.fillRect(r.translated(3, 3), QColor(0, 0, 0, 90))
            p.fillRect(r, page_bg)
            p.save()
            p.setClipRect(r)
            self._draw_images(p, i, X, Y, dpr, vw * dpr, vh * dpr, s_now, sk)
            self._draw_overlays(p, i)
            p.restore()
        p.end()

    def _draw_images(self, p, i, X, Y, dpr, VW, VH, s_now, sk):
        inv = self.invert
        T = PdfEngine.TILE
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        pw, ph = self.sizes[i]
        pv = self.cache.get(("p", i, inv))
        exact_full = abs(sk - s_now) < 1e-3 and self._covered(i, sk, X, Y, VW, VH)
        if pv is not None and not exact_full:
            p.drawImage(QRectF(X / dpr, Y / dpr, pw * self.zoom, ph * self.zoom), pv)
        scales = self.scales.get(i, {})
        others = sorted((k for k in scales if abs(k - s_now) >= 1e-3), key=lambda k: -abs(math.log(k / s_now)))
        if not exact_full:
            for k in others:
                f = s_now / k                       # ladrilho na escala k -> tela agora
                tx0, ty0, tx1, ty1 = self._tile_range(i, k, X / f, Y / f, 0, 0, VW / f, VH / f)
                for ty in range(ty0, ty1 + 1):
                    for tx in range(tx0, tx1 + 1):
                        img = self.cache.get(("t", i, k, tx, ty, inv))
                        if img is not None:
                            p.drawImage(QRectF((X + tx * T * f) / dpr, (Y + ty * T * f) / dpr,
                                               img.width() * f / dpr, img.height() * f / dpr), img)
        if abs(sk - s_now) < 1e-3:
            p.setRenderHint(QPainter.SmoothPixmapTransform, False)
            tx0, ty0, tx1, ty1 = self._tile_range(i, sk, X, Y, 0, 0, VW, VH)
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    key = ("t", i, sk, tx, ty, inv)
                    img = self.cache.get(key)
                    if img is not None:
                        self.cache.move_to_end(key)
                        img.setDevicePixelRatio(dpr)
                        p.drawImage(QPointF((X + tx * T) / dpr, (Y + ty * T) / dpr), img)

    def _covered(self, i, sk, X, Y, VW, VH):
        tx0, ty0, tx1, ty1 = self._tile_range(i, sk, X, Y, 0, 0, VW, VH)
        inv = self.invert
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                if ("t", i, sk, tx, ty, inv) not in self.cache:
                    return False
        return True

    def _draw_overlays(self, p, i):
        p.setRenderHint(QPainter.Antialiasing, True)
        pt = self.texts.get(i)
        if pt is None:
            return
        hs = self.hits.get(i)
        if hs:
            p.setPen(Qt.NoPen)
            p.setBrush(HIT_FILL)
            for a, b in hs[:2000]:
                if self.cur_hit == (i, a, b):
                    continue
                for r in pt.rects(a, b):
                    p.drawRect(self._rect_to_view(i, r).adjusted(-1, -1, 1, 1))
        if self.cur_hit is not None and self.cur_hit[0] == i:
            _, a, b = self.cur_hit
            rs = [self._rect_to_view(i, r).adjusted(-2, -2, 2, 2) for r in pt.rects(a, b)]
            p.setBrush(CUR_FILL)
            p.setPen(QPen(CUR_LINE, 1.6))
            for r in rs:
                p.drawRoundedRect(r, 2, 2)
            pm = self.pin_mark
            if pm is not None and pm[0] == i:
                prs = [self._rect_to_view(i, r).adjusted(-2, -2, 2, 2) for r in pm[1]]
                p.setBrush(PIN_FILL)
                p.setPen(QPen(PIN_LINE, 2.2))
                for r in prs:
                    p.drawRoundedRect(r, 2, 2)
                rs = prs[:1] or rs                   # o "flash" vai em volta do pino
            if self._flash_t0 is not None and rs:
                t = (time.monotonic() - self._flash_t0) / 1.1
                if t < 1:
                    u = rs[0]
                    for r in rs[1:]:
                        u = u.united(r)
                    grow = 6 + 46 * (1 - t) ** 2
                    c = QColor(CUR_LINE)
                    c.setAlphaF(max(0.0, 1 - t))
                    p.setBrush(Qt.NoBrush)
                    p.setPen(QPen(c, 3))
                    p.drawRoundedRect(u.adjusted(-grow, -grow, grow, grow), 6, 6)
        if self.sel is not None and self.sel[0] == i:
            _, a, b = self.sel
            p.setBrush(SEL_FILL)
            p.setPen(QPen(SEL_LINE, 1))
            for r in pt.rects(a, b):
                p.drawRect(self._rect_to_view(i, r).adjusted(-0.5, -0.5, 0.5, 0.5))

    def _flash_step(self):
        if self._flash_t0 is None or time.monotonic() - self._flash_t0 > 1.15:
            self._flash_t0 = None
            self._flash_timer.stop()
        self.viewport().update()

    def set_invert(self, on):
        if bool(on) == self.invert:
            return
        self.invert = bool(on)
        self._clear_cache()
        self._req_timer.start()
        self.viewport().update()

    # ------------------------------------------------------------------ texto / seleção
    def _text_ready(self, g, i, pt):
        if g != self.gen:
            return
        old = self.texts.get(i)
        if old is not None and old.has_boxes and not pt.has_boxes:
            return
        self.texts[i] = pt
        if self.term:
            self.hits[i] = pt.find(self.term, self.whole)
            self._rebuild_order()
        pg = self._pending_goto
        if pg is not None and pg[0] == i and pt.has_boxes:
            self._pending_goto = None
            self.goto_hit(pg)
        if self.term:
            self._advance_search()
        self.viewport().update()

    def _char_at(self, pos, nearest=False, page=None):
        hp = self.hit_page(pos, clamp=True)
        if hp is None:
            return None
        i, u, v, inside = hp
        if page is not None and i != page:
            # arrastando para fora da página da seleção: usa a beirada dela
            i = page
            q = pos.y() + self._origin()[1]
            v = (q - self.page_y[i]) / self.zoom
            u = (pos.x() + self._origin()[0] - self.page_x[i]) / self.zoom
        pt = self.texts.get(i)
        if pt is None:
            return None
        k = pt.char_at(u, v, tol=max(1.5 / self.zoom, 0.6))
        if k is None and nearest:
            k = pt.nearest_char(u, v)
        return None if k is None else (i, k)

    def selected_text(self):
        if self.sel is None:
            return ""
        i, a, b = self.sel
        pt = self.texts.get(i)
        return pt.text_range(a, b).strip() if pt is not None else ""

    def copy(self):
        t = self.selected_text()
        if t:
            QGuiApplication.clipboard().setText(t)
        return t

    def clear_selection(self):
        if self.sel is not None:
            self.sel = None
            self.selectionChanged.emit("")
            self.viewport().update()

    def select_all_page(self):
        i = self.cur_page
        pt = self.texts.get(i)
        if pt is not None and len(pt):
            self.sel = (i, 0, len(pt) - 1)
            self._sel_changed()

    def _sel_changed(self):
        t = self.selected_text()
        if t:
            cb = QGuiApplication.clipboard()
            if cb.supportsSelection():
                cb.setText(t, QClipboard.Selection)
        self.selectionChanged.emit(t)
        self.viewport().update()

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.position()
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self.eng is not None):
            hit = self._char_at(pos) if e.button() == Qt.LeftButton else None
            now = time.monotonic()
            if hit is not None and now - self._last_dbl < QApplication.doubleClickInterval() / 1000.0:
                i, k = hit                                   # triplo clique: a linha
                a, b = self.texts[i].line_at(k)
                self.sel = (i, a, b)
                self._mode = None
                self._sel_changed()
                e.accept()
                return
            if hit is not None:
                i, k = hit
                if e.modifiers() & Qt.ShiftModifier and self.sel is not None and self.sel[0] == i:
                    self.sel = (i, self.sel[1], k)
                else:
                    self.sel = (i, k, k)
                self._mode = "select"
                self._press = (pos, False)
                self.viewport().setCursor(Qt.IBeamCursor)
                self.viewport().update()
            else:
                self._mode = "pan"
                self._press = (pos, self.horizontalScrollBar().value(), self.verticalScrollBar().value())
                self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._mode == "pan":
            p0, h0, v0 = self._press
            d = pos - p0
            self.horizontalScrollBar().setValue(int(h0 - d.x()))
            self.verticalScrollBar().setValue(int(v0 - d.y()))
        elif self._mode == "select":
            p0, moved = self._press
            if not moved and (pos - p0).manhattanLength() >= 3:
                self._press = (p0, True)
            i = self.sel[0]
            hit = self._char_at(pos, nearest=True, page=i)
            if hit is not None and hit[1] != self.sel[2]:
                self.sel = (i, self.sel[1], hit[1])
                self.viewport().update()
            # rolar sozinho perto da borda
            vp = self.viewport()
            vb = self.verticalScrollBar()
            if pos.y() < 0:
                vb.setValue(vb.value() + int(pos.y() / 2) - 1)
            elif pos.y() > vp.height():
                vb.setValue(vb.value() + int((pos.y() - vp.height()) / 2) + 1)
        else:
            self.viewport().setCursor(Qt.IBeamCursor if self._char_at(pos) is not None else Qt.OpenHandCursor)
        e.accept()

    def mouseReleaseEvent(self, e):
        if self._mode == "select":
            _, moved = self._press
            if not moved and self.sel is not None and self.sel[1] == self.sel[2]:
                self.sel = None                              # só um clique: limpa
                self.selectionChanged.emit("")
                self.viewport().update()
            else:
                self._sel_changed()
        self._mode = None
        self._press = None
        self.viewport().setCursor(Qt.IBeamCursor if self._char_at(e.position()) is not None else Qt.OpenHandCursor)
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            hit = self._char_at(e.position())
            if hit is not None:
                i, k = hit
                a, b = self.texts[i].word_at(k)
                self.sel = (i, a, b)
                self._mode = None
                self._last_dbl = time.monotonic()
                self._sel_changed()
            else:
                self._mode = "pan"
                self._press = (e.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        e.accept()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.zoom_by(1.0015 ** d, e.position())
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self.copy()
            return
        if e.matches(QKeySequence.SelectAll):
            self.select_all_page()
            return
        k = e.key()
        if k == Qt.Key_Escape:
            self.clear_selection()
            return
        if k == Qt.Key_Home and not e.modifiers():
            self.verticalScrollBar().setValue(0)
            return
        if k == Qt.Key_End and not e.modifiers():
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
            return
        if k == Qt.Key_Space:
            vb = self.verticalScrollBar()
            vb.setValue(vb.value() + (-1 if e.modifiers() & Qt.ShiftModifier else 1) * vb.pageStep())
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e):
        m = QMenu(self)
        txt = self.selected_text()
        word = ""
        if not txt:
            hit = self._char_at(QPointF(e.pos()))
            if hit is not None:
                i, k = hit
                word = self.texts[i].text_range(*self.texts[i].word_at(k)).strip()
        a = m.addAction("Copiar", self.copy)
        a.setShortcut(QKeySequence.Copy)
        a.setEnabled(bool(txt))
        if word:
            m.addAction(f"Copiar “{word}”", lambda w=word: QGuiApplication.clipboard().setText(w))
        q = (txt.split() or [""])[0] if txt else word
        if q and len(q) <= 60:
            m.addAction(f"Mostrar “{q}” na placa", lambda q=q: self.lookupRequested.emit(q))
            m.addAction(f"Procurar “{q}” em outras placas e esquemas…", lambda q=q: self.donorRequested.emit(q))
        m.addAction("Selecionar todo o texto da página", self.select_all_page)
        m.addSeparator()
        m.addAction("Ajustar largura", lambda: self.fit("width"))
        m.addAction("Ajustar página", lambda: self.fit("page"))
        m.exec(e.globalPos())

    # ------------------------------------------------------------------ busca
    def clear_search(self):
        self._pending_goto = None
        self.pin_target = None
        self.pin_mark = None
        self.pin_status = ""
        self._pin_first = False
        self.term = ""
        self.hits = {}
        self.order = []
        self.cur_hit = None
        self.search_done = True
        self._jump_pending = False
        self.searchChanged.emit()

    def search(self, term, whole=True, start_page=0, pin=None):
        self.clear_search()
        if not term or self.eng is None:
            return
        if pin is not None:
            self.pin_target = pin
            self._pin_first = True
            self._pin_auto = True                # 1º salto: se o pino não estiver nesta folha, tenta as outras
            self._pin_tried = set()
            self._pin_origin = None
        self.term = term
        self.whole = whole
        self._start_page = max(0, min(int(start_page), len(self.sizes) - 1))
        self.search_done = False
        self._jump_pending = True
        for i, pt in self.texts.items():
            self.hits[i] = pt.find(term, whole)
        self._rebuild_order()
        self._advance_search()

    def search_pin(self, part, num, net, pin_count=99):
        """Procura o componente e, nele, o pino: número do pino perto do componente (de preferência com o
        nome da rede ao lado). Se o pino não aparecer no texto, fica no componente."""
        num = (num or "").strip()
        net = (net or "").strip()
        if net.upper() in ("", "UNCONNECTED", "NC", "N/C") or net.upper().startswith("UNNAMED") or net.startswith("N$"):
            net = ""
        self.pin_count = int(pin_count or 99)
        self.search(part, whole=True, pin=(num, net))

    def _rebuild_order(self):
        out = []
        for i in sorted(self.hits):
            pt = self.texts[i]
            hs = self.hits[i]
            if not hs:
                continue
            keyed = []
            for a, b in hs:
                if pt.has_boxes:               # ordem de leitura: de cima para baixo, da esquerda para a direita
                    bx = pt.boxes[a]
                    keyed.append((round(float(bx[1]) / 4), float(bx[0]), a, b))
                else:
                    keyed.append((0, float(a), a, b))
            keyed.sort()
            out.extend((i, a, b) for _, _, a, b in keyed)
        self.order = out

    def _advance_search(self):
        n = len(self.sizes)
        done = all(i in self.texts for i in range(n))
        if done and not self.order and self.whole and self.term:
            # só aparece dentro de outra palavra: mostra assim mesmo
            self.whole = False
            for i, pt in self.texts.items():
                self.hits[i] = pt.find(self.term, False)
            self._rebuild_order()
        if self._jump_pending and self.pin_target is not None:
            if done:
                self._jump_pending = False
                if self.order:
                    self.goto_hit(self._best_pin_hit())
        if self._jump_pending and self.pin_target is None:
            st = getattr(self, "_start_page", 0)
            for i in list(range(st, n)) + list(range(0, st)):
                if i not in self.texts:
                    break                      # ainda lendo esta página: espera
                if self.hits.get(i):
                    first = next(h for h in self.order if h[0] == i)
                    self._jump_pending = False
                    self.goto_hit(first)
                    break
        if done:
            self.search_done = True
            self._jump_pending = False
            self._req_timer.start()
        self.searchChanged.emit()
        self.viewport().update()

    def _best_pin_hit(self):
        """Primeira ocorrência do componente numa página onde a rede (ou o nº) do pino também aparece."""
        num, net = self.pin_target
        st = getattr(self, "_start_page", 0)
        pages = sorted({h[0] for h in self.order}, key=lambda i: (i < st, i))
        for probe in ([net] if net else []) + ([num] if num and not num.isdigit() else []):
            for i in pages:
                if self.texts[i].find(probe, True):
                    return next(h for h in self.order if h[0] == i)
        return next(h for h in self.order if h[0] == pages[0])

    @staticmethod
    def _spots(pt, hs):
        """[(a, b, centro u, centro v, altura da letra, retângulos)] das ocorrências."""
        out = []
        for a, b in hs:
            rs = pt.rects(a, b)
            if not rs:
                continue
            u0 = min(r[0] for r in rs)
            v0 = min(r[1] for r in rs)
            u1 = max(r[2] for r in rs)
            v1 = max(r[3] for r in rs)
            h = max(min(min(r[2] - r[0], r[3] - r[1]) for r in rs), 0.5)
            out.append((a, b, (u0 + u1) / 2, (v0 + v1) / 2, h, rs))
        return out

    def _locate_pin(self, i, pt, hit, whole_page):
        """Acha o pino na página i. Devolve (ocorrência do componente, retângulos do pino, texto) ou None.
        Prioridade: nº do pino com o nome da rede logo ao lado > nome da rede > nº do pino sozinho, sempre o
        mais perto do nome do componente."""
        num, net = self.pin_target
        parts = self._spots(pt, self.hits.get(i, []) if whole_page else [(hit[1], hit[2])])
        if not parts:
            return None
        nums = self._spots(pt, pt.find(num, True)) if num else []
        nets = self._spots(pt, pt.find(net, True)) if net else []
        pw, ph = self.sizes[i]
        far = 0.6 * math.hypot(pw, ph)
        best = None
        for P in parts:
            h = P[4]
            d = lambda s, P=P: math.hypot(s[2] - P[2], s[3] - P[3])
            for m in nums:
                dm = d(m)
                if dm > far:
                    continue
                if nets:
                    n = min(nets, key=lambda n: math.hypot(n[2] - m[2], n[3] - m[3]))
                    dmn = math.hypot(n[2] - m[2], n[3] - m[3])
                    # o nº é do pino quando o rótulo da rede está perto dele (mais perto que do componente)
                    if dmn < 20 * h and dmn < d(n):
                        c = (0, dm + 2 * dmn, P, m[5] + n[5], f"pino {num} ({net})")
                        best = c if best is None or c[:2] < best[:2] else best
                        continue
                # nº sozinho: só em CI/conector (em resistor e capacitor o esquema não costuma numerar)
                if self.pin_count > 3 and dm < 0.4 * far:
                    c = (2, dm, P, m[5], f"pino {num}")
                    best = c if best is None or c[:2] < best[:2] else best
            for n in nets:
                dn = d(n)
                if dn < far:
                    c = (1, dn, P, n[5], f"rede {net} do pino {num}" if num else f"rede {net}")
                    best = c if best is None or c[:2] < best[:2] else best
        if best is None:
            return None
        P = best[2]
        return (i, P[0], P[1]), best[3], best[4]

    def hit_index(self):
        try:
            return self.order.index(self.cur_hit)
        except ValueError:
            return -1

    def step(self, d):
        if not self.order:
            return
        k = self.hit_index()
        k = (k + d) % len(self.order) if k >= 0 else (0 if d > 0 else len(self.order) - 1)
        self.goto_hit(self.order[k])

    def goto_hit(self, hit, min_px=13.0, target_px=22.0):
        i, a, b = hit
        pt = self.texts.get(i)
        if pt is None:
            return
        if not pt.has_boxes:
            # ainda sem as caixas das letras desta página: pede já e pula quando chegarem
            self._pending_goto = hit
            self.cur_hit = hit
            self._req_timer.start()
            self.searchChanged.emit()
            return
        rs = pt.rects(a, b)
        self.pin_mark = None
        if self.pin_target is not None:
            first = self.cur_hit is None or self.cur_hit[0] != i or self._pin_first
            found = self._locate_pin(i, pt, hit, whole_page=first)
            self._pin_first = False
            if found is None and getattr(self, "_pin_auto", False):
                # componente dividido em folhas (U4A, U4B...): procura o pino nas outras folhas dele
                if self._pin_origin is None:
                    self._pin_origin = hit
                self._pin_tried.add(i)
                nxt = next((h for h in self.order if h[0] not in self._pin_tried), None)
                if nxt is not None:
                    self._pin_first = True
                    self.goto_hit(nxt, min_px, target_px)
                    return
                self._pin_auto = False
                hit = self._pin_origin
                i, a, b = hit
                pt = self.texts[i]
                rs = pt.rects(a, b)
            self._pin_auto = False
            if found is not None:
                hit, prs, what = found
                i, a, b = hit
                self.pin_mark = (i, prs)
                rs = prs[:1]                     # enquadra pelo nº do pino (ou pela rede)
                self.pin_status = f"{self.term}: {what}"
            else:
                self.pin_status = f"{self.term}: o pino {self.pin_target[0]} não aparece no texto desta folha " \
                                  f"— mostrando o componente"
        if not rs:
            return
        u0 = min(r[0] for r in rs)
        v0 = min(r[1] for r in rs)
        u1 = max(r[2] for r in rs)
        v1 = max(r[3] for r in rs)
        # altura da letra (o lado menor: vale para texto deitado e em pé)
        size = max(min(min(r[2] - r[0], r[3] - r[1]) for r in rs), 0.5)
        if size * self.zoom < min_px:
            z = min(target_px / size, 12 * self._pt_px())
            self.fit_mode = None
            if abs(z - self.zoom) > 1e-6:
                self.zoom = z
                self._relayout()
                self.zoomChanged.emit()
            self._zoom_settled()
        self.cur_hit = hit
        self.center_on(i, (u0 + u1) / 2, (v0 + v1) / 2)
        self._flash_t0 = time.monotonic()
        self._flash_timer.start()
        self.searchChanged.emit()
        self.viewport().update()


class SchematicWindow(QMainWindow):
    pdfChosen = Signal(str)          # o usuário abriu outro PDF nesta janela
    lookupRequested = Signal(str)    # "mostrar na placa"
    donorRequested = Signal(str)     # "procurar em outras placas e esquemas"

    def __init__(self, parent=None):
        # janela independente (sem "dono"): no Windows ganha botão próprio na barra de tarefas, agrupado no
        # ícone do programa, e minimiza para lá — com dono, ela minimizava para o canto da tela
        super().__init__(None, Qt.Window)
        self._owner = parent
        self.setWindowTitle("Esquema elétrico")
        self.resize(1100, 850)
        self.setAcceptDrops(True)
        self.path = ""
        self._settings = getattr(parent, "settings", None) or QSettings()
        self.view = PdfCanvas(self)
        self.setCentralWidget(self.view)
        v = self.view
        v.searchChanged.connect(self._update_label)
        v.pageChanged.connect(self._page_changed)
        v.zoomChanged.connect(self._zoom_changed)
        v.selectionChanged.connect(self._selection_changed)
        v.lookupRequested.connect(self.lookupRequested)
        v.donorRequested.connect(self.donorRequested)

        tb = QToolBar("Esquema")
        tb.setMovable(False)
        self.addToolBar(tb)
        a = QAction("Abrir PDF…", self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.choose_pdf)
        tb.addAction(a)
        tb.addSeparator()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Procurar no esquema (componente, rede…)")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        self.search.returnPressed.connect(self._search_enter)
        tb.addWidget(self.search)
        f = QAction("Procurar", self)
        f.setShortcut(QKeySequence.Find)
        f.triggered.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.addAction(f)
        prev_a = QAction("◀", self)
        prev_a.setToolTip("Resultado anterior (Shift+F3)")
        prev_a.setShortcut("Shift+F3")
        prev_a.triggered.connect(lambda: self.step(-1))
        next_a = QAction("▶", self)
        next_a.setToolTip("Próximo resultado (F3)")
        next_a.setShortcut("F3")
        next_a.triggered.connect(lambda: self.step(1))
        tb.addAction(prev_a)
        tb.addAction(next_a)
        self.result_lbl = QLabel("")
        self.result_lbl.setMinimumWidth(120)
        tb.addWidget(self.result_lbl)
        tb.addSeparator()
        zo = QAction("−", self)
        zo.setShortcut("Ctrl+-")
        zo.setToolTip("Diminuir (Ctrl+−)")
        zo.triggered.connect(lambda: v.zoom_by(1 / 1.25))
        tb.addAction(zo)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setInsertPolicy(QComboBox.NoInsert)
        self.zoom_box.setMinimumWidth(130)
        self.zoom_box.addItems(["Ajustar largura", "Ajustar página"] + [f"{z} %" for z in ZOOMS])
        self.zoom_box.activated.connect(self._zoom_chosen)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_typed(self.zoom_box.currentText()))
        tb.addWidget(self.zoom_box)
        zi = QAction("+", self)
        zi.setShortcuts([QKeySequence("Ctrl+="), QKeySequence("Ctrl++")])
        zi.setToolTip("Aumentar (Ctrl++)")
        zi.triggered.connect(lambda: v.zoom_by(1.25))
        tb.addAction(zi)
        fw = QAction("Largura", self)
        fw.setShortcut("Ctrl+0")
        fw.setToolTip("Ajustar à largura (Ctrl+0)")
        fw.triggered.connect(lambda: v.fit("width"))
        tb.addAction(fw)
        fp = QAction("Página", self)
        fp.setShortcut("Ctrl+9")
        fp.setToolTip("Página inteira (Ctrl+9)")
        fp.triggered.connect(lambda: v.fit("page"))
        tb.addAction(fp)
        tb.addSeparator()
        self.inv_a = QAction("Inverter cores", self)
        self.inv_a.setCheckable(True)
        self.inv_a.setToolTip("Fundo escuro (cores invertidas), para ler à noite")
        self.inv_a.toggled.connect(self._invert)
        tb.addAction(self.inv_a)
        sp = QWidget()
        sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(sp)
        tb.addWidget(QLabel("Página "))
        self.page_box = QSpinBox()
        self.page_box.setMinimum(1)
        self.page_box.setMaximum(1)
        self.page_box.setKeyboardTracking(False)
        self.page_box.valueChanged.connect(lambda n: v.go_page(n - 1) if n - 1 != v.cur_page else None)
        tb.addWidget(self.page_box)
        self.page_lbl = QLabel(" de 0  ")
        tb.addWidget(self.page_lbl)
        self.statusBar().showMessage("Duplo clique num componente (ou num pino) da placa para achar ele aqui.  "
                                     "Arrastar no texto: seleciona (Ctrl+C copia) · fora do texto: move · "
                                     "Ctrl+roda: zoom")
        geo = self._settings.value("schematic_window/geometry")
        if geo:
            self.restoreGeometry(geo)
        inv = self._settings.value("schematic/invert", False)
        self.inv_a.setChecked(inv in (True, "true", "1", 1))

    # ------------------------------------------------------------------ arquivo
    def choose_pdf(self):
        start = os.path.dirname(self.path) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Esquema elétrico (PDF)", start,
                                              "PDF (*.pdf);;Todos os arquivos (*)")
        if path and self.load(path):
            self.pdfChosen.emit(path)

    def show_doc_pdf(self, path):
        """Mostra o PDF da aba atual do boardview (ou nada, se a aba não tiver)."""
        if path and os.path.exists(path):
            if os.path.abspath(path) != os.path.abspath(self.path or ""):
                self.load(path)
        elif path != self.path:
            self.view.close_doc()
            self.path = ""
            self._page_changed(-1)
            self.setWindowTitle("Esquema elétrico — (esta aba não tem PDF; use Abrir PDF…)")

    def load(self, path):
        try:
            self.view.open(path)
        except Exception as e:  # noqa
            msg = str(e)
            if "password" in msg.lower() or "senha" in msg.lower():
                msg = "O PDF está protegido por senha."
            QMessageBox.warning(self, "Esquema", f"Não consegui abrir o PDF:\n{path}\n\n{msg}")
            return False
        self.path = path
        self.setWindowTitle(f"Esquema — {os.path.basename(path)}")
        self.page_box.setMaximum(max(1, self.view.page_count()))
        self._page_changed(0)
        self._update_label()
        return True

    def dragEnterEvent(self, e):
        if any(u.toLocalFile().lower().endswith(".pdf") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".pdf"):
                if self.load(p):
                    self.pdfChosen.emit(p)
                break

    # ------------------------------------------------------------------ barra
    def _page_changed(self, i):
        n = self.view.page_count()
        self.page_lbl.setText(f" de {n}  ")
        if i >= 0:
            self.page_box.blockSignals(True)
            self.page_box.setValue(i + 1)
            self.page_box.blockSignals(False)

    def _zoom_changed(self):
        v = self.view
        txt = {"width": "Ajustar largura", "page": "Ajustar página"}.get(v.fit_mode) or f"{v.zoom_percent():.0f} %"
        if not self.zoom_box.lineEdit().hasFocus():
            self.zoom_box.setEditText(txt)

    def _zoom_chosen(self, k):
        t = self.zoom_box.itemText(k)
        if t.startswith("Ajustar largura"):
            self.view.fit("width")
        elif t.startswith("Ajustar página"):
            self.view.fit("page")
        else:
            self._zoom_typed(t)
        self.view.setFocus()

    def _zoom_typed(self, t):
        try:
            pct = float(t.replace("%", "").replace(",", ".").strip())
        except ValueError:
            self._zoom_changed()
            return
        self.view.set_zoom_percent(max(5.0, min(pct, 4000.0)))
        self.view.setFocus()

    def _invert(self, on):
        self.view.set_invert(on)
        self._settings.setValue("schematic/invert", bool(on))

    def _selection_changed(self, t):
        if t:
            short = t if len(t) <= 60 else t[:57] + "…"
            self.statusBar().showMessage(f"Selecionado: “{short}” — Ctrl+C copia · botão direito: mostrar na placa")
        else:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------------ busca
    def _search_enter(self):
        t = self.search.text().strip()
        if t and t.upper() == self.view.term.upper() and self.view.order:
            self.step(1)
        else:
            self.find(t, "text")

    def find(self, term, kind="part"):
        """Procura 'term' (palavra inteira). Repetir o mesmo termo vai para a próxima ocorrência."""
        v = self.view
        if not term or v.eng is None:
            return
        if term.upper() == v.term.upper() and v.order:
            v.step(1)
            self.raise_()
            return
        self.search.setText(term)
        v.search(term, whole=True)
        self.raise_()

    def find_pin(self, part, num, net, pin_count=99):
        """Duplo clique num pino da placa: vai até o pino 'num' do componente 'part' (rede 'net').
        Repetir no mesmo pino vai para a próxima vez que o componente aparece."""
        v = self.view
        if not part or v.eng is None:
            return
        if part.upper() == v.term.upper() and v.order and v.pin_target is not None \
                and v.pin_target[0] == (num or "").strip():
            v.step(1)
            self.raise_()
            return
        self.search.setText(part)
        v.search_pin(part, num, net, pin_count)
        self.raise_()

    def step(self, d):
        self.view.step(d)

    def open_hit(self, path, term, page=0):
        """Abre um PDF (pode ser de outra placa) e vai até 'term' a partir da página dada."""
        if os.path.abspath(path) != os.path.abspath(self.path or ""):
            if not self.load(path):
                return
            self.statusBar().showMessage(f"Esquema de outra placa: {os.path.basename(path)}", 10000)
        self.search.setText(term)
        self.view.search(term, whole=True, start_page=page)
        self.show()
        self.raise_()
        self.activateWindow()

    def _update_label(self):
        v = self.view
        if not v.term:
            self.result_lbl.setText("")
            return
        tot = len(v.order)
        more = "" if v.search_done else "…"
        if tot == 0:
            self.result_lbl.setText("procurando…" if not v.search_done else "não encontrado")
            if v.search_done:
                self.statusBar().showMessage(f"“{v.term}” não aparece no texto do PDF "
                                             f"(o esquema pode ser uma imagem escaneada).", 8000)
        else:
            k = v.hit_index()
            part = "" if v.whole else " (parte de palavra)"
            self.result_lbl.setText(f"{k + 1 if k >= 0 else '–'} de {tot}{more}{part}")
            if v.pin_status and v.pin_status != getattr(self, "_shown_pin", ""):
                self._shown_pin = v.pin_status
                self.statusBar().showMessage(v.pin_status, 10000)

    def closeEvent(self, e):
        self._settings.setValue("schematic_window/geometry", self.saveGeometry())
        self.view.trim_cache()
        super().closeEvent(e)
