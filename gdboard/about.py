"""Janela "Sobre": escura, com a arte em ASCII, os créditos e os links (mesmo estilo dos outros
programas da GDTech)."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

BG = "#14171c"
TXT = "#e6e6e6"
DIM = "#8a93a3"
AMBER = "#ffb300"
AMBER_HI = "#ffc933"
INK = "#0e1013"
LINK = "#4aa3ff"

ABOUT_ART = r"""                         CRACK!! .----------------.   ________
                            \ | /|  .----------.  |  |  ____  |
   O     .--.         .--.   \|/ |  | X      X |  |  | |____| |
  /|\=-.'    '-------'    '-.*   |  |   ~~~~   |  |  |  ____  |
   |                         /|\ |  '----------'  |  | |____| |
  / \                       / | \'----------------'  |   ()   |
 /   \                                _|____|_       |  [__]  |
                                     |________|      |________|"""

LINKS = (("YouTube:", "youtube.com/GDTechinformatica", "https://youtube.com/GDTechinformatica"),
         ("Instagram:", "instagram.com/thegregin", "https://instagram.com/thegregin"))


def _mono_font(size: int) -> QFont:
    for fam in ("Consolas", "Cascadia Mono", "Courier New", "DejaVu Sans Mono", "Liberation Mono"):
        if fam in QFontDatabase.families():
            f = QFont(fam, size)
            break
    else:
        f = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        f.setPointSize(size)
    f.setStyleHint(QFont.TypeWriter)
    return f


def _ui_font(size: int, bold: bool = False) -> QFont:
    f = QFont("Segoe UI", size)
    f.setBold(bold)
    return f


def dark_title_bar(w: QWidget):
    """Barra de título escura no Windows 10/11 (no resto não faz nada)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(w.winId())
        on = ctypes.c_int(1)
        for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 nas builds antigas do Win10)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
    except Exception:  # noqa
        pass


class _Link(QLabel):
    """Link azul sublinhado, fica âmbar com o mouse em cima e abre no navegador."""

    def __init__(self, text: str, url: str, parent=None):
        super().__init__(text, parent)
        self._url = url
        self.setFont(_ui_font(9))
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(url)
        self._paint(LINK)

    def _paint(self, color: str):
        self.setStyleSheet(f"color:{color}; text-decoration: underline; background: transparent;")

    def enterEvent(self, e):
        self._paint(AMBER)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._paint(LINK)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self._url))
        super().mouseReleaseEvent(e)


class AboutDialog(QDialog):
    def __init__(self, parent=None, icon=None):
        super().__init__(parent)
        self.setWindowTitle("Sobre")
        self.setWindowFlags(Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
        if icon is not None:
            self.setWindowIcon(icon)
        self.setObjectName("about")
        self.setStyleSheet(f"QDialog#about {{ background: {BG}; }} QLabel {{ background: transparent; }}")
        self.setAutoFillBackground(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(0)

        art = QLabel(ABOUT_ART)
        art.setFont(_mono_font(10))
        art.setStyleSheet(f"color:{TXT};")
        art.setTextFormat(Qt.PlainText)
        art.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        lay.addWidget(art, 0, Qt.AlignHCenter)

        def label(text, color, size, bold, top, bottom=0):
            lb = QLabel(text)
            lb.setFont(_ui_font(size, bold))
            lb.setStyleSheet(f"color:{color};")
            lb.setAlignment(Qt.AlignCenter)
            lay.addSpacing(top)
            lay.addWidget(lb, 0, Qt.AlignHCenter)
            if bottom:
                lay.addSpacing(bottom)
            return lb

        label("usando IA do jeito certo", AMBER, 15, True, 16, 2)
        label("esse software foi 100% vibecoded com Claude.ai", DIM, 9, False, 10)
        label('"criado" por GDTech Informática', TXT, 9, True, 10, 4)

        for cap, text, url in LINKS:
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 1, 0, 1)
            hl.setSpacing(6)
            c = QLabel(cap)
            c.setFont(_ui_font(9))
            c.setStyleSheet(f"color:{DIM};")
            hl.addWidget(c)
            hl.addWidget(_Link(text, url))
            lay.addWidget(row, 0, Qt.AlignHCenter)

        lay.addSpacing(16)
        btn = QPushButton("Fechar")
        btn.setFont(_ui_font(9, True))
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            f"QPushButton {{ background:{AMBER}; color:{INK}; border:none; padding:4px 20px; border-radius:2px; }}"
            f"QPushButton:hover {{ background:{AMBER_HI}; }}"
            f"QPushButton:pressed {{ background:{AMBER_HI}; }}"
            f"QPushButton:focus {{ outline: none; }}")
        btn.setDefault(True)
        btn.clicked.connect(self.close)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        self.btn = btn

        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def showEvent(self, e):
        super().showEvent(e)
        dark_title_bar(self)
        p = self.parentWidget()
        if p is not None:
            g = p.frameGeometry()
            self.move(g.center().x() - self.width() // 2, g.center().y() - self.height() // 2)
