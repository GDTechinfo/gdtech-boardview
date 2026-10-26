"""Renderizador por CPU (QPainter). Usado quando OpenGL não está disponível."""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF, QLineF, Signal, QTimer
from PySide6.QtGui import (QColor, QPainter, QPainterPath, QPen, QBrush, QPolygonF, QTransform,
                           QFont, QFontMetricsF)
from PySide6.QtWidgets import QWidget

from .core import CanvasCore, CAT_NORMAL, CAT_TH, CAT_GND, CAT_NC, CAT_TP
from .parsers import TOP, BOTTOM, BOTH
from .theme import BUILTIN


def _qc(c, alpha_mul=1.0):
    if c is None:
        return None
    return QColor(c[0], c[1], c[2], int(c[3] * alpha_mul))


class BoardCanvas(QWidget, CanvasCore):
    selectionChanged = Signal()
    cursorMoved = Signal(float, float)
    viewChanged = Signal()
    fpsChanged = Signal(float)
    itemDoubleClicked = Signal(str, str)     # ("part" | "net", nome) — usado pelo esquema em PDF
    contextRequested = Signal(int, int, object)   # componente, pino (-1 = nenhum), posição global
    pinStepped = Signal(int, int)            # Tab entre pinos da rede: posição, total
    renderer_name = "CPU"

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        self._core_init()
        self.theme = BUILTIN["GDTech Escuro"]
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 200)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self._timer = QTimer(self)
        self._timer.setInterval(8)
        self._timer.timeout.connect(self._on_timer)
        self._cache_key = None
        self._caches = {}
        self._frames = 0
        self._fps_t = time.perf_counter()

    # ------------------------------------------------------------------ CanvasCore
    def request_frame(self):
        self.update()
        if self.animating() and not self._timer.isActive():
            self._timer.start()

    def _on_timer(self):
        if not self.tick():
            self._timer.stop()
        self.update()
        self.viewChanged.emit()

    def board_changed(self):
        self._cache_key = None
        self._caches = {}

    def theme_changed(self):
        self._cache_key = None
        self._caches = {}
        self.update()

    # ------------------------------------------------------------------ eventos
    def wheelEvent(self, e):
        self.core_wheel(e)

    def mousePressEvent(self, e):
        self.core_press(e)

    def mouseMoveEvent(self, e):
        self.core_move(e)

    def mouseReleaseEvent(self, e):
        self.core_release(e)

    def mouseDoubleClickEvent(self, e):
        self.core_double(e)

    def focusNextPrevChild(self, nxt):
        return False        # Tab é usado para ir ao próximo pino da rede, não para trocar o foco

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.update()

    # ------------------------------------------------------------------ cache por lado
    def _build_cache(self):
        b = self.board
        th = self.theme
        o = self.opts
        key = (self.side, id(th), th.colorize, o.pin_scale, o.square_pins, o.show_mech)
        if self._cache_key == key:
            return
        hit = self._caches.get(key)
        if hit is not None:
            self.__dict__.update(hit)
            self._cache_key = key
            return
        if len(self._caches) > 4:
            self._caches = {}
        self._cache_key = key
        path = QPainterPath()
        for a, c in b.outline_segs:
            path.moveTo(a[0], a[1])
            path.lineTo(c[0], c[1])
        self._outline_path = path
        self._ppoly = [QPolygonF([QPointF(x, y) for x, y in p.outline]) for p in b.parts]
        # corpo dos componentes agrupado por cor + contornos
        groups = {}
        boxes = QPainterPath()
        for i, p in enumerate(b.parts):
            if not (p.side == self.side or p.side == BOTH) or len(p.outline) < 2:
                continue
            if p.is_mech and not o.show_mech:
                continue                    # peça mecânica escondida (Exibir → Mostrar peças mecânicas)
            fill = th.get(self.ptype[i]) if (th.colorize and self.ptype[i]) else None
            if fill is not None:
                body = QPolygonF([QPointF(x, y) for x, y in p.body]) if p.body else self._ppoly[i]
                groups.setdefault(fill, QPainterPath()).addPolygon(body)
            boxes.addPolygon(self._ppoly[i])
            boxes.closeSubpath()
        self._part_groups = groups
        self._boxes = boxes
        self._vis = (b.side == self.side) | (b.side == BOTH)
        if not o.show_mech:
            self._vis &= ~b.pin_mech_dead          # furos soltos de peça mecânica escondida
        # pads agrupados por cor
        idx = np.nonzero(self._vis)[0]
        self._pad_poly = {}
        polys = pad_polygons(b, idx, o.pin_scale, o.square_pins)
        for i, pg in zip(idx, polys):
            self._pad_poly[int(i)] = pg
        cols = self._pin_colors(idx)
        groups = {}
        for i, col in zip(idx, cols):
            groups.setdefault(col, QPainterPath()).addPolygon(self._pad_poly[int(i)])
        self._pin_groups = list(groups.items())
        self._caches[key] = {k: getattr(self, k) for k in ['_outline_path', '_ppoly', '_part_groups', '_boxes', '_vis', '_pad_poly', '_pin_groups']}

    def _pad_path(self, idx):
        path = QPainterPath()
        for i in idx:
            pg = self._pad_poly.get(int(i))
            if pg is not None:
                path.addPolygon(pg)
                path.closeSubpath()
        return path

    def _pin_colors(self, idx):
        th = self.theme
        base = th.get("PinsPads")
        out = []
        for i in idx:
            c = self.pcat[i]
            col = base
            if c == CAT_GND:
                col = th.get("GndColor", "PinsPads")
            elif c == CAT_NC:
                col = th.get("NcColor", "PinsPads")
            elif c == CAT_TP:
                col = th.get("TestPinColor", "PinsPads")
            elif self.first_pin[i] and th.get("FirstPinColor") is not None:
                col = th.get("FirstPinColor")
            out.append(tuple(col))
        return out

    def hover_updated(self):
        self.update()

    def layers_updated(self):
        self.update()

    def marks_updated(self):
        self.update()

    # ------------------------------------------------------------------ camadas de cobre (CPU)
    def _layer_cache(self, i):
        cache = self.__dict__.setdefault("_lay_cache", {})
        key = (id(self.board), i)
        c = cache.get(key)
        if c is not None:
            return c
        L = self.board.layers[i]
        c = {"lines": [], "pour": None, "vias": None}
        if len(L.seg):
            w = np.round(L.seg[:, 4], 2)
            for wv in np.unique(w):
                sel = L.seg[w == wv]
                c["lines"].append((float(wv), [QLineF(a, b_, cc, d) for a, b_, cc, d in sel[:, :4].tolist()]))
        if L.polys:
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for _net, rings in L.polys:
                for r in rings:
                    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in r.tolist()]))
                    path.closeSubpath()
            c["pour"] = path
        if len(L.pad):
            path = QPainterPath()
            for x, y, hw, hh, _r in L.pad.tolist():
                rr = min(hw, hh)
                path.addEllipse(QPointF(x, y), rr, rr)
            c["vias"] = path
        cache[key] = c
        return c

    def _paint_layers(self, p):
        b = self.board
        order = list(range(len(b.layers)))
        if self.side == TOP:
            order = order[::-1]
        dim = self.net_active()
        k = 0.45 if dim else 1.0
        for i in order:
            if not self.layer_visible(i):
                continue
            c = self._layer_cache(i)
            r, g, bl = self.layer_color(i)
            if c["pour"] is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(r, g, bl, int(255 * 0.30 * k)))
                p.drawPath(c["pour"])
            for wv, lines in c["lines"]:
                pen = QPen(QColor(r, g, bl, int(255 * 0.9 * k)), max(wv, 0.0), Qt.SolidLine, Qt.RoundCap)
                if wv * self.scale < 0.8:
                    pen = QPen(QColor(r, g, bl, int(255 * 0.9 * k)), 0.8)
                    pen.setCosmetic(True)
                p.setPen(pen)
                p.drawLines(lines)
            if c["vias"] is not None:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(int(r * 0.6 + 102), int(g * 0.6 + 102), int(bl * 0.6 + 102), int(255 * 0.85 * k)))
                p.drawPath(c["vias"])
        if dim:
            hc = _qc(self.theme.get("NetHighlight"))
            net = self.sel_net
            for i in order:
                if not self.layer_visible(i):
                    continue
                L = b.layers[i]
                if len(L.seg):
                    sel = L.seg[L.seg_net == net]
                    for wv in np.unique(np.round(sel[:, 4], 2)):
                        ss = sel[np.round(sel[:, 4], 2) == wv]
                        pen = QPen(hc, max(float(wv), 1.2 / self.scale), Qt.SolidLine, Qt.RoundCap)
                        p.setPen(pen)
                        p.drawLines([QLineF(a, b_, cc, d) for a, b_, cc, d in ss[:, :4].tolist()])
                if len(L.pad):
                    sp = L.pad[L.pad_net == net]
                    p.setPen(Qt.NoPen)
                    p.setBrush(hc)
                    for x, y, hw, hh, _r in sp.tolist():
                        rr = max(min(hw, hh), 1.5 / self.scale)
                        p.drawEllipse(QPointF(x, y), rr, rr)
                if L.polys and not b.net_is_gnd[net]:
                    path = QPainterPath()
                    path.setFillRule(Qt.OddEvenFill)
                    for n_, rings in L.polys:
                        if n_ == net:
                            for r in rings:
                                path.addPolygon(QPolygonF([QPointF(x, y) for x, y in r.tolist()]))
                                path.closeSubpath()
                    c2 = QColor(hc)
                    c2.setAlpha(115)
                    p.setPen(Qt.NoPen)
                    p.setBrush(c2)
                    p.drawPath(path)

    def leaveEvent(self, e):
        self.core_leave()

    # ------------------------------------------------------------------ desenho
    def paintEvent(self, e):
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            if p.isActive():
                p.end()
        self._frames += 1
        now = time.perf_counter()
        if now - self._fps_t >= 0.5:
            self.fpsChanged.emit(self._frames / (now - self._fps_t))
            self._frames = 0
            self._fps_t = now

    def _paint(self, p):
        th = self.theme
        p.fillRect(self.rect(), _qc(th.get("Background")))
        b = self.board
        if b is None:
            p.setPen(QColor(150, 155, 165))
            f = p.font()
            f.setPointSize(13)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "Arraste um arquivo de boardview aqui\nou use Arquivo → Abrir (Ctrl+O)")
            return
        copies = [(self.side, None)]
        if self.dual_view:
            copies.append((self.other_side(), self.mirror_R()))
        side0 = self.side
        for cur_side, R in copies:
            self.side = cur_side
            self._copy_R = R
            try:
                self._paint_copy(p)
            finally:
                self.side = side0
                self._copy_R = None
                p.resetTransform()
        if (self.dual_view and self.netweb_cross_active() and self.opts.show_netweb
                and self.sel_net is not None and not self.board.net_is_nc[self.sel_net]):
            M = self.matrix()
            p.setTransform(QTransform(M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2]))
            pen = QPen(_qc(self.theme.get("NetwebCurrent")), 1.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            for x0, y0, x1, y1 in self.netweb_scene_segments()[:3000]:
                p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
            p.resetTransform()

    def _paint_copy(self, p):
        th = self.theme
        b = self.board
        self._build_cache()
        M = self.matrix()
        T = QTransform(M[0, 0], M[1, 0], M[0, 1], M[1, 1], M[0, 2], M[1, 2])
        self._T = T
        s = self.scale
        o = self.opts
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setTransform(T)

        fill = th.get("BoardFill")
        if fill is not None:
            x0, y0, x1, y1 = b.bounds
            p.fillRect(QRectF(x0, y0, x1 - x0, y1 - y0), _qc(fill))

        lay_on = self.layer_mode and self.has_layers()
        if lay_on:
            self._paint_layers(p)

        if o.show_boxes:
            p.setPen(Qt.NoPen)
            for col, path in (self._part_groups.items() if not lay_on else ()):
                p.setBrush(QBrush(_qc(col)))
                p.drawPath(path)
            box = th.get("ComponentBox")
            if box is not None:
                pen = QPen(_qc(box), 1.0)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(self._boxes)

        pen = QPen(_qc(th.get("BoardOutline")), 1.6)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(self._outline_path)

        # componentes na rede selecionada
        if self.sel_net is not None and not b.net_is_gnd[self.sel_net]:
            parts = {int(b.part[i]) for i in b.net_pins[self.sel_net] if b.part[i] >= 0}
            pen = QPen(_qc(th.get("HighlightedPart")), 1.6)
            pen.setCosmetic(True)
            p.setPen(pen)
            for pi in parts:
                if self.pside[pi] in (self.side, BOTH):
                    p.drawPolygon(self._ppoly[pi])

        for marks, fill_a in ((self.note_marks, 0.30), (self.part_marks, 0.22)):
            for pi, c in marks.items():
                if 0 <= pi < len(self._ppoly) and self.pside[pi] in (self.side, BOTH):
                    pen = QPen(_qc(c), 1.6)
                    pen.setCosmetic(True)
                    p.setPen(pen)
                    p.setBrush(QBrush(_qc(c, fill_a)))
                    p.drawPolygon(self._ppoly[pi])

        if self.sel_part is not None:
            c = th.get("SelectedPart")
            pen = QPen(_qc(c), 2.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QBrush(_qc(c, 0.18)))
            p.drawPolygon(self._ppoly[self.sel_part])

        # pads (cada um na sua cor, com borda clara)
        net_active = self.net_active()
        for col, path in self._pin_groups:
            qc = _qc(col, 0.45 if net_active else 1.0)
            pen = QPen(_border(col, 0.45 if net_active else 1.0), 1.0)
            pen.setCosmetic(True)
            p.setPen(pen if s > 0.5 else Qt.NoPen)
            p.setBrush(QBrush(qc))
            p.drawPath(path)

        if self.group_nets:
            gc = th.get("NetwebOther") or (51, 153, 255, 255)
            gi = [i for ni in self.group_nets if ni != self.sel_net and 0 <= ni < len(b.net_pins)
                  for i in b.net_pins[ni] if self._vis[i]]
            pen = QPen(_border(gc), 1.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QBrush(QColor(gc[0], gc[1], gc[2])))
            p.drawPath(self._pad_path(gi[:20000]))

        if net_active:
            hl = [i for i in b.net_pins[self.sel_net] if self._vis[i]]
            if o.show_netweb and self.sel_pin is not None and not b.net_is_gnd[self.sel_net]:
                pen = QPen(_qc(th.get("NetwebCurrent")), 1.0)
                pen.setCosmetic(True)
                p.setPen(pen)
                if self.netweb_cross_active():
                    pass                            # desenhadas depois das duas placas (_paint)
                else:
                    x0, y0 = float(b.pad_x[self.sel_pin]), float(b.pad_y[self.sel_pin])
                    for i in hl[:3000]:
                        if i != self.sel_pin:
                            p.drawLine(QPointF(x0, y0), QPointF(float(b.pad_x[i]), float(b.pad_y[i])))
            nh = th.get("NetHighlight")
            pen = QPen(_border(nh), 1.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QBrush(_qc(nh)))
            p.drawPath(self._pad_path(hl[:20000]))
        elif self.sel_net is not None and b.net_is_gnd[self.sel_net] and o.gnd_subtle:
            # GND discreto: só o contorno dos pinos
            pen = QPen(_qc(th.get("NetHighlight")), 1.3)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(self._pad_path([i for i in b.net_pins[self.sel_net] if self._vis[i]][:20000]))

        if o.show_loose:
            lc = th.get("LoosePin") or (225, 95, 225, 255)
            pen = QPen(_qc(lc), 1.4)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(self._pad_path([i for i in np.nonzero(b.pin_loose & self._vis)[0]][:20000]))
            p.setBrush(QBrush(QColor(lc[0], lc[1], lc[2], 60)))
            for pi, nonet in enumerate(b.part_no_net):
                if nonet and self.pside[pi] in (self.side, BOTH) and (o.show_mech or not b.parts[pi].is_mech):
                    p.drawPolygon(self._ppoly[pi])

        p.setBrush(Qt.NoBrush)
        if self.sel_part is not None:
            pen = QPen(_qc(th.get("SelectedPart")), 1.6)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(self._pad_path([i for i in b.parts[self.sel_part].pins if self._vis[i]]))

        if self.sel_pin is not None and self._vis[self.sel_pin]:
            pen = QPen(_qc(th.get("SelectedPin")), 2.4)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(self._pad_path([self.sel_pin]))

        hc = th.get("HoverPart")
        if hc is not None and self.hover_part is not None and self.hover_part != self.sel_part \
                and self.pside[self.hover_part] in (self.side, BOTH):
            pen = QPen(_qc(hc), 1.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPolygon(self._ppoly[self.hover_part])

        p.resetTransform()
        if o.show_part_labels:
            self._draw_part_names(p)
        if o.show_pin_labels or o.show_net_names:
            self._draw_pin_texts(p)

    def _draw_part_names(self, p):
        b = self.board
        if not b.parts:
            return
        s = self.scale
        x0, y0, x1, y1 = self.visible_rect()
        side_ok = (self.pside == self.side) | (self.pside == BOTH)
        scr = self.psize * s
        bx0, by0, bx1, by1 = b.bounds
        side_ok = side_ok & (self.psize < 0.35 * max(bx1 - bx0, by1 - by0))
        if not self.opts.show_mech:
            side_ok = side_ok & ~np.array([p.is_mech for p in b.parts], dtype=bool)
        m = side_ok & (scr > 14) & (self.pcx >= x0 - self.psize) & (self.pcx <= x1 + self.psize) & \
            (self.pcy >= y0 - self.psize) & (self.pcy <= y1 + self.psize)
        idx = np.nonzero(m)[0]
        if len(idx) > 2500:
            idx = idx[np.argsort(-scr[idx])[:2500]]
        font = QFont(self.font())
        font.setBold(True)
        T = self._T
        col = _qc(self.theme.get("ComponentLabelText"))
        M = self.matrix()[:2, :2]
        for i in idx:
            i = int(i)
            part = b.parts[i]
            name = part.name
            L = part.dims[0]
            W = getattr(part, "label_w", part.dims[1])    # peça mecânica: nome do tamanho de um CI comum
            avail = L
            if part.body:
                q0, q1 = part.body[0], part.body[1]
                avail = math.hypot(q1[0] - q0[0], q1[1] - q0[1])
            avail = max(avail, W * 0.5)
            font.setPixelSize(40)
            fm = QFontMetricsF(font)
            lw = fm.horizontalAdvance(name) / 40.0
            single = len(part.pins) == 1
            if single:
                fpx = min(W * s * 0.3, 13.0) * self.opts.part_label_scale
            else:
                fpx = min(W * s * 0.4 * self.opts.part_label_scale, avail * s * 0.86 / max(lw, 0.01))
            if fpx < 6:
                continue
            fpx = min(fpx, 110.0)
            font.setPixelSize(max(1, int(round(fpx))))
            p.setFont(font)
            c = T.map(QPointF(float(part.center[0]), float(part.center[1])))
            ang = 0.0
            if not single:
                d = M @ np.array(part.axis)
                if d[0] < -0.02 or (abs(d[0]) <= 0.02 and d[1] > 0):
                    d = -d
                ang = math.degrees(math.atan2(d[1], d[0]))
            p.save()
            p.translate(c.x(), c.y() - ((W * s * 0.5 + fpx * 0.75) if single else 0))
            p.rotate(ang)
            w = lw * fpx * 1.1 + 4
            rect = QRectF(-w / 2, -fpx, w, 2 * fpx)
            p.setPen(QColor(0, 0, 0, 170))
            p.drawText(rect.translated(1, 1), Qt.AlignCenter, name)
            p.setPen(col)
            p.drawText(rect, Qt.AlignCenter, name)
            p.restore()

    def _draw_pin_texts(self, p):
        b = self.board
        s = self.scale
        g = self.opts.pin_scale
        x0, y0, x1, y1 = self.visible_rect()
        hw = b.pad_hw * g
        hh = b.pad_hh * g
        rnd = b.pad_round > 0.5
        hw = np.where(rnd, np.minimum(hw, hh) * 0.75, hw)
        hh = np.where(rnd, np.minimum(b.pad_hw * g, hh) * 0.75, hh)
        padp = 2 * np.minimum(hw, hh) * s
        m = self._vis & (padp > 12) & (b.pad_x >= x0) & (b.pad_x <= x1) & (b.pad_y >= y0) & (b.pad_y <= y1)
        idx = np.nonzero(m)[0]
        if len(idx) == 0 or len(idx) > 4000:
            return
        T = self._T
        M = self.matrix()[:2, :2]
        font = QFont(self.font())
        font.setBold(True)
        pin_col = _qc(self.theme.get("PinLabelText"))
        net_col = _qc(self.theme.get("NetNameText", "PinLabelText"))
        o = self.opts
        from .glview import text_shadow
        shc = text_shadow(self.theme.get("PinLabelText"))
        pin_sh = QColor(int(shc[0] * 255), int(shc[1] * 255), int(shc[2] * 255), int(shc[3] * 255))
        for i in idx:
            i = int(i)
            ax = M @ np.array([b.pad_ux[i], b.pad_uy[i]])
            ax /= max(np.hypot(*ax), 1e-9)
            availW = 2 * (abs(ax[0]) * hw[i] + abs(ax[1]) * hh[i]) * s
            pp = float(padp[i])
            num = b.pname[i] if b.pname[i] and len(b.pname[i]) <= 5 else b.num[i]
            fnum = min(pp * 0.42, 18.0, availW * 0.9 / max(0.62 * len(num), 0.6)) * o.pin_label_scale
            fnet = min(pp * 0.2, 10.0) * o.pin_label_scale
            show_num = o.show_pin_labels and fnum >= 5
            net = b.nets[b.net[i]]
            show_net = o.show_net_names and pp >= 26 and fnet >= 5 and net != "UNCONNECTED"
            c = T.map(QPointF(float(b.pad_x[i]), float(b.pad_y[i])))
            if show_num:
                font.setPixelSize(max(1, int(round(fnum))))
                p.setFont(font)
                cy = c.y() - (fnet * 0.62 if show_net else 0)
                rect = QRectF(c.x() - 200, cy - fnum, 400, 2 * fnum)
                p.setPen(pin_sh)
                p.drawText(rect.translated(1, 1), Qt.AlignCenter, num)
                p.setPen(pin_col)
                p.drawText(rect, Qt.AlignCenter, num)
            if show_net:
                fn = fnet
                font.setPixelSize(max(1, int(round(fn))))
                p.setFont(font)
                txt = elide(QFontMetricsF(font), net, availW * 1.15)
                if not txt:
                    continue
                cy = c.y() + (fnum * 0.55 if show_num else 0)
                rect = QRectF(c.x() - 200, cy - fn, 400, 2 * fn)
                p.setPen(net_col)
                p.drawText(rect, Qt.AlignCenter, txt)


def elide(fm, text, avail):
    """Corta o texto para caber em 'avail' pixels, terminando em '..' — quanto mais zoom, mais letras."""
    if fm.horizontalAdvance(text) <= avail:
        return text
    lim = avail - fm.horizontalAdvance("..")
    lo, hi = 0, len(text)
    while lo < hi:                      # maior prefixo que cabe
        mid = (lo + hi + 1) // 2
        if fm.horizontalAdvance(text[:mid]) <= lim:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ".." if lo > 0 else ""


def _border(col, alpha_mul=1.0):
    r, g, b_, a = col
    lum = (0.299 * r + 0.587 * g + 0.114 * b_) / 255
    if lum > 0.62:
        r, g, b_ = int(r * 0.7), int(g * 0.7), int(b_ * 0.7)
    else:
        r, g, b_ = int(r + (255 - r) * 0.32), int(g + (255 - g) * 0.32), int(b_ + (255 - b_) * 0.32)
    return QColor(r, g, b_, int(a * alpha_mul))


def pad_polygons(b, idx, grow=1.0, square=False, arc_steps=4):
    """Contorno de cada pad (retângulo com cantos arredondados) em coordenadas da placa."""
    idx = np.asarray(idx, dtype=np.int64)
    out = []
    if len(idx) == 0:
        return out
    hw = b.pad_hw[idx] * grow
    hh = b.pad_hh[idx] * grow
    rr = np.zeros(len(idx)) if square else np.clip(b.pad_round[idx], 0, 1) * np.minimum(hw, hh)
    ux, uy = b.pad_ux[idx], b.pad_uy[idx]
    cx, cy = b.pad_x[idx], b.pad_y[idx]
    # 4 cantos x (arc_steps+1) pontos
    t = np.linspace(0, math.pi / 2, arc_steps + 1)
    base = []
    for k, (sx, sy) in enumerate(((1, 1), (-1, 1), (-1, -1), (1, -1))):
        a0 = k * math.pi / 2
        base.append((sx, sy, np.cos(a0 + t), np.sin(a0 + t)))
    for j in range(len(idx)):
        r = rr[j]
        if r <= 0:
            lx = np.array([hw[j], -hw[j], -hw[j], hw[j]])
            ly = np.array([hh[j], hh[j], -hh[j], -hh[j]])
        else:
            lx = np.concatenate([sx * (hw[j] - r) + r * ca for sx, sy, ca, sa in base])
            ly = np.concatenate([sy * (hh[j] - r) + r * sa for sx, sy, ca, sa in base])
        X = cx[j] + ux[j] * lx - uy[j] * ly
        Y = cy[j] + uy[j] * lx + ux[j] * ly
        out.append(QPolygonF([QPointF(float(x), float(y)) for x, y in zip(X, Y)]))
    return out
