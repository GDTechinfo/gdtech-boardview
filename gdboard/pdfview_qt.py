"""Visualizador antigo (reserva, só QtPdf) do esquema elétrico, ligado à placa: duplo clique num componente do boardview
procura o mesmo componente no esquema e vai até ele (com destaque).

Usa o QtPdf do PySide6 (PySide6 >= 6.6)."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, QTimer, QPointF, QModelIndex, Signal, QSettings
from PySide6.QtGui import QKeySequence, QAction
from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (QMainWindow, QToolBar, QLineEdit, QLabel, QFileDialog, QMessageBox, QWidget,
                               QSizePolicy)

_ALNUM = re.compile(r"[A-Za-z0-9_]")


class _View(QPdfView):
    """QPdfView com zoom no Ctrl+roda (mantendo o ponto do mouse) e arrastar com o botão esquerdo/meio."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag = None

    def zoom_by(self, f, pos=None):
        old = self.zoomFactor()
        new = max(0.1, min(old * f, 40.0))
        if abs(new - old) < 1e-6:
            return
        vp = self.viewport()
        mx = pos.x() if pos is not None else vp.width() / 2
        my = pos.y() if pos is not None else vp.height() / 2
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        hx, vy = h.value() + mx, v.value() + my
        self.setZoomMode(QPdfView.ZoomMode.Custom)
        self.setZoomFactor(new)
        r = new / old
        h.setValue(int(hx * r - mx))
        v.setValue(int(vy * r - my))

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            d = e.angleDelta().y()
            if d:
                self.zoom_by(1.0015 ** d, e.position())
            e.accept()
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._drag = (e.position(), self.horizontalScrollBar().value(), self.verticalScrollBar().value())
            self.viewport().setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            p0, h0, v0 = self._drag
            d = e.position() - p0
            self.horizontalScrollBar().setValue(int(h0 - d.x()))
            self.verticalScrollBar().setValue(int(v0 - d.y()))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.viewport().unsetCursor()
            e.accept()
            return
        super().mouseReleaseEvent(e)


class SchematicWindow(QMainWindow):
    pdfChosen = Signal(str)       # o usuário abriu outro PDF nesta janela

    MIN_ZOOM_ON_JUMP = 1.6        # zoom mínimo ao pular para um componente (para dar para ler)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Esquema elétrico")
        self.resize(1100, 850)
        self.path = ""
        self.doc = QPdfDocument(self)
        self.view = _View(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.setCentralWidget(self.view)
        self.model = QPdfSearchModel(self)
        self.model.setDocument(self.doc)
        self._has_highlight = hasattr(self.view, "setSearchModel")
        if self._has_highlight:
            self.view.setSearchModel(self.model)

        self._term = ""
        self._exact = []            # índices (no modelo) dos resultados com a palavra inteira
        self._seen = 0
        self._cur = -1
        self._want_jump = False
        self._idle = 0
        self._poll = QTimer(self)
        self._poll.setInterval(60)
        self._poll.timeout.connect(self._collect)

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
        self.search.setMinimumWidth(260)
        self.search.returnPressed.connect(lambda: self.find(self.search.text().strip(), "text"))
        tb.addWidget(self.search)
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
        self.result_lbl.setMinimumWidth(140)
        tb.addWidget(self.result_lbl)
        tb.addSeparator()
        for txt, f, key in (("−", 1 / 1.25, "Ctrl+-"), ("+", 1.25, "Ctrl+=")):
            z = QAction(txt, self)
            z.setShortcut(key)
            z.triggered.connect(lambda checked=False, f=f: self.view.zoom_by(f))
            tb.addAction(z)
        fw = QAction("Ajustar largura", self)
        fw.setShortcut("Ctrl+0")
        fw.triggered.connect(lambda: self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth))
        tb.addAction(fw)
        sp = QWidget()
        sp.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(sp)
        self.page_lbl = QLabel("")
        tb.addWidget(self.page_lbl)
        try:
            self.view.pageNavigator().currentPageChanged.connect(self._page_changed)
        except AttributeError:
            pass
        self.statusBar().showMessage("Duplo clique num componente da placa para achar ele aqui.  "
                                     "Ctrl+roda: zoom · arrastar: mover")
        self._settings = getattr(parent, "settings", None) or QSettings()
        geo = self._settings.value("schematic_window/geometry")
        if geo:
            self.restoreGeometry(geo)

    # ------------------------------------------------------------------ arquivo
    def choose_pdf(self):
        start = os.path.dirname(self.path) if self.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Esquema elétrico (PDF)", start,
                                              "PDF (*.pdf);;Todos os arquivos (*)")
        if path:
            if self.load(path):
                self.pdfChosen.emit(path)

    def show_doc_pdf(self, path):
        """Mostra o PDF da aba atual do boardview (ou nada, se a aba não tiver)."""
        if path and os.path.exists(path):
            if os.path.abspath(path) != os.path.abspath(self.path or ""):
                self.load(path)
        elif path != self.path:
            self.doc.close()
            self.path = ""
            self.setWindowTitle("Esquema elétrico — (esta aba não tem PDF; use Abrir PDF…)")

    def load(self, path):
        self._reset_search()
        self.doc.close()
        self.doc.load(path)
        if self.doc.pageCount() <= 0:
            QMessageBox.warning(self, "Esquema", f"Não consegui abrir o PDF:\n{path}")
            self.path = ""
            return False
        self.path = path
        self.setWindowTitle(f"Esquema — {os.path.basename(path)}")
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self._page_changed(0)
        return True

    def _page_changed(self, page):
        n = self.doc.pageCount()
        self.page_lbl.setText(f"Página {page + 1} de {n}  " if n > 0 else "")

    # ------------------------------------------------------------------ busca
    def _reset_search(self):
        self._poll.stop()
        self._term = ""
        self._exact = []
        self._seen = 0
        self._cur = -1
        self.result_lbl.setText("")

    def find(self, term, kind="part"):
        """Procura 'term' (palavra inteira). Repetir o mesmo termo vai para a próxima ocorrência."""
        if not term or self.doc.pageCount() <= 0:
            return
        if term.upper() == self._term.upper() and self._exact:
            self.step(1)
            return
        self._reset_search()
        self._term = term
        self.search.setText(term)
        self._want_jump = True
        self._idle = 0
        self.result_lbl.setText("procurando…")
        self.model.setSearchString(term)
        self._collect()
        self._poll.start()

    def _context(self, i, link):
        before = after = ""
        try:
            before, after = link.contextBefore(), link.contextAfter()
        except AttributeError:
            try:
                idx = self.model.index(i, 0)
                before = self.model.data(idx, QPdfSearchModel.Role.ContextBefore.value) or ""
                after = self.model.data(idx, QPdfSearchModel.Role.ContextAfter.value) or ""
            except Exception:  # noqa
                pass
        return str(before or ""), str(after or "")

    def _is_whole_word(self, i, link):
        before, after = self._context(i, link)
        if before and _ALNUM.match(before[-1]):
            return False
        if after and _ALNUM.match(after[0]):
            return False
        return True

    def _collect(self):
        """A busca do QtPdf roda aos poucos (página por página): junta os resultados que já chegaram."""
        n = self.model.rowCount(QModelIndex())
        if n > self._seen:
            for i in range(self._seen, n):
                link = self.model.resultAtIndex(i)
                if self._is_whole_word(i, link):
                    self._exact.append(i)
            self._seen = n
            self._idle = 0
            if self._want_jump and self._exact:
                self._want_jump = False
                self._goto(0)
        else:
            self._idle += 1
        self._update_label(done=False)
        # terminou: nada novo por ~2 s (ou ~20 s no total sem nada)
        if (self._seen > 0 and self._idle > 33) or self._idle > 330:
            self._poll.stop()
            if self._want_jump:
                self._want_jump = False
                if self._seen > 0:            # só achou como parte de outra palavra: usa o primeiro mesmo
                    self._exact = list(range(self._seen))
                    self._goto(0)
            self._update_label(done=True)

    def _update_label(self, done):
        if not self._term:
            self.result_lbl.setText("")
            return
        tot = len(self._exact)
        if tot == 0:
            self.result_lbl.setText("não encontrado" if done else "procurando…")
            if done:
                self.statusBar().showMessage(f"“{self._term}” não aparece no texto do PDF "
                                             f"(o esquema pode ser uma imagem escaneada).", 8000)
        else:
            self.result_lbl.setText(f"{self._cur + 1} de {tot}{'' if done else '…'}")

    def step(self, d):
        if self._exact:
            self._goto((self._cur + d) % len(self._exact))

    def _goto(self, k):
        self._cur = k
        i = self._exact[k]
        link = self.model.resultAtIndex(i)
        page = link.page()
        loc = link.location()
        if self.view.zoomMode() != QPdfView.ZoomMode.Custom or self.view.zoomFactor() < self.MIN_ZOOM_ON_JUMP:
            self.view.setZoomMode(QPdfView.ZoomMode.Custom)
            self.view.setZoomFactor(max(self.view.zoomFactor(), self.MIN_ZOOM_ON_JUMP))
        if self._has_highlight:
            try:
                self.view.setCurrentSearchResultIndex(i)
            except Exception:  # noqa
                pass
        self.view.pageNavigator().jump(page, QPointF(loc.x(), loc.y()), self.view.zoomFactor())
        # o jump deixa o ponto no canto de cima/esquerda: centraliza na tela
        QTimer.singleShot(0, self._center_after_jump)
        self._update_label(done=not self._poll.isActive())
        self.raise_()

    def _center_after_jump(self):
        vp = self.view.viewport()
        h, v = self.view.horizontalScrollBar(), self.view.verticalScrollBar()
        h.setValue(int(h.value() - vp.width() / 2 + 40))
        v.setValue(int(v.value() - vp.height() / 2 + 20))

    def closeEvent(self, e):
        self._settings.setValue("schematic_window/geometry", self.saveGeometry())
        super().closeEvent(e)
