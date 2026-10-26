"""Caixinha de informações que aparece ao passar o mouse sobre um componente ou pino."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QGraphicsDropShadowEffect
from PySide6.QtGui import QColor


class HoverCard(QFrame):
    DELAY_MS = 120

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("HoverCard")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "#HoverCard { background: rgba(38,38,42,242); border: 1px solid #4A4A50; border-radius: 5px; }"
            "QLabel { color: #E8E8EA; background: transparent; font-size: 12px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 7, 12, 8)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(False)
        lay.addWidget(self.label)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(14)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(sh)
        self.key = None
        self._pending = None
        self._pos = (0, 0)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_pending)
        self.hide()

    def show_for(self, key, html_fn, x, y):
        """key identifica o alvo (muda o conteúdo só quando o alvo muda); html_fn gera o texto."""
        self._pos = (int(x), int(y))
        if key == self.key and self.isVisible():
            self._place()
            return
        if key == self.key and self._timer.isActive():
            return
        self.key = key
        self._pending = html_fn
        if self.isVisible():
            self._show_pending()        # já estava aberto: troca na hora
        else:
            self._timer.start(self.DELAY_MS)

    def _show_pending(self):
        if self._pending is None:
            return
        self.label.setText(self._pending())
        self._pending = None
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()

    def _place(self):
        par = self.parentWidget()
        if par is None:
            return
        w, h = self.width(), self.height()
        mx, my = self._pos
        x, y = mx + 18, my + 20
        if x + w > par.width() - 4:
            x = mx - w - 12
        if y + h > par.height() - 4:
            y = my - h - 12
        self.move(max(4, x), max(4, y))

    def hide_card(self):
        self._timer.stop()
        self._pending = None
        self.key = None
        self.hide()
