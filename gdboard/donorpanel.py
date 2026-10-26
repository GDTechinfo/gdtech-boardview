"""Janela "Peça em outras placas": procura o componente em todos os boardviews e esquemas da biblioteca
(para tirar de uma placa de sucata). O índice é atualizado em segundo plano (donor.Indexer)."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt, Signal, QObject, QTimer
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QComboBox,
                               QCheckBox, QTreeWidget, QTreeWidgetItem, QStackedWidget, QHeaderView, QSizePolicy)

from . import donor

SIDE = {1: "Top", 2: "Bottom", 3: "T/B"}


class DonorIndex(QObject):
    """Mantém o índice atualizado num processo separado (donorproc): a interface não engasga enquanto ele lê."""
    progress = Signal(int, int, str)
    finished = Signal(int, int)

    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self._h = None
        self._busy = False                 # do start até chegar o aviso de fim
        self._again = None
        self.finished.connect(self._done, Qt.QueuedConnection)

    def running(self):
        return self._busy

    def start(self, roots, keys=None):
        if not roots:
            return
        if self.running():
            self._again = (list(roots), keys)        # termina este e roda de novo com as pastas novas
            return
        from . import donorproc
        self._busy = True
        self._h = donorproc.start(self.db_path, roots, keys,
                                  on_progress=lambda k, n, p: self.progress.emit(k, n, p),
                                  on_done=lambda b, p: self.finished.emit(b, p))

    def _done(self, *_):
        self._busy = False
        if self._again is not None:
            roots, keys = self._again
            self._again = None
            QTimer.singleShot(500, lambda: self.start(roots, keys))

    def stop(self):
        if self._h is not None:
            self._h.stop(wait=2.0)


class _Searcher(QObject):
    done = Signal(int, object)


class DonorWindow(QWidget):
    openBoard = Signal(str, str)          # caminho, componente
    openPdf = Signal(str, str, int)       # caminho, texto, página
    wantFolder = Signal()                 # pedir para escolher a pasta da biblioteca
    reindexRequested = Signal()

    def __init__(self, index: DonorIndex, roots_fn, parent=None):
        super().__init__(None, Qt.Window)    # independente: minimiza para a barra de tarefas (ver SchematicWindow)
        self._owner = parent
        self.setWindowTitle("Peça em outras placas")
        self.resize(900, 640)
        self.index = index
        self.roots_fn = roots_fn
        self.origin = ""
        self._gen = 0
        self._searcher = _Searcher()
        self._searcher.done.connect(self._show, Qt.QueuedConnection)
        index.progress.connect(self._progress)
        index.finished.connect(self._index_done)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        self.head = QLabel("Procure um componente em todos os seus boardviews e esquemas (pasta da Biblioteca).")
        self.head.setWordWrap(True)
        self.head.setStyleSheet("font-size: 13px;")
        lay.addWidget(self.head)

        row = QHBoxLayout()
        self.mode = QComboBox()
        self.mode.addItems(["Código do fabricante", "Valor e encapsulamento"])
        self.mode.setToolTip("Código: CIs, transistores, diodos (ex.: uP9512R, L2N7002).\n"
                             "Valor: capacitores, resistores, indutores (ex.: 22uF 0603).")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        row.addWidget(self.mode)
        self.fields = QStackedWidget()
        # código
        w1 = QWidget()
        l1 = QHBoxLayout(w1)
        l1.setContentsMargins(0, 0, 0, 0)
        self.code = QLineEdit()
        self.code.setPlaceholderText("código do componente, ex.: uP9512R, L2N7002, U_DRMOS_QFN5X5")
        self.code.returnPressed.connect(self.run)
        l1.addWidget(self.code)
        self.fields.addWidget(w1)
        # valor
        w2 = QWidget()
        l2 = QHBoxLayout(w2)
        l2.setContentsMargins(0, 0, 0, 0)
        self.vtype = QComboBox()
        self._types = list(donor.PASSIVE_TYPES.items())
        self.vtype.addItems([t[1] for t in self._types])
        self.value = QLineEdit()
        self.value.setPlaceholderText("valor, ex.: 22uF, 10k, 0.22uH")
        self.value.returnPressed.connect(self.run)
        self.pkg = QLineEdit()
        self.pkg.setPlaceholderText("encapsulamento (0402, 0603…) — vazio = qualquer")
        self.pkg.returnPressed.connect(self.run)
        l2.addWidget(self.vtype)
        l2.addWidget(self.value, 1)
        l2.addWidget(self.pkg, 1)
        self.fields.addWidget(w2)
        row.addWidget(self.fields, 1)
        self.go = QPushButton("Procurar")
        self.go.setDefault(True)
        self.go.clicked.connect(self.run)
        row.addWidget(self.go)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.in_boards = QCheckBox("Boardviews")
        self.in_boards.setChecked(True)
        self.in_pdfs = QCheckBox("Esquemas (PDF)")
        self.in_pdfs.setChecked(True)
        for cb in (self.in_boards, self.in_pdfs):
            cb.toggled.connect(lambda _on: self.run())
            row2.addWidget(cb)
        self.summary = QLabel("")
        self.summary.setStyleSheet("color: #9aa3af;")
        row2.addWidget(self.summary, 1)
        lay.addLayout(row2)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Onde", "Componente / trecho", "Descrição"])
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemActivated.connect(self._open)
        self.tree.itemDoubleClicked.connect(self._open)
        hd = self.tree.header()
        hd.setSectionResizeMode(0, QHeaderView.Interactive)
        hd.setStretchLastSection(True)
        self.tree.setColumnWidth(0, 330)
        self.tree.setColumnWidth(1, 220)
        lay.addWidget(self.tree, 1)

        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: #9aa3af;")
        self.status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        foot.addWidget(self.status, 1)
        self.b_folder = QPushButton("Pastas da biblioteca…")
        self.b_folder.clicked.connect(self.wantFolder.emit)
        self.b_reindex = QPushButton("Atualizar índice")
        self.b_reindex.clicked.connect(self.reindexRequested.emit)
        foot.addWidget(self.b_folder)
        foot.addWidget(self.b_reindex)
        lay.addLayout(foot)
        hint = QLabel("Dois cliques num resultado: abre a placa já com o componente selecionado, ou o esquema na "
                      "página certa.")
        hint.setStyleSheet("color: #7d8696;")
        lay.addWidget(hint)
        self._update_status()

    # ------------------------------------------------------------------ entrada
    def ask(self, q: donor.Query, origin_path=""):
        """Abre a janela já com a consulta do componente e procura."""
        self.origin = origin_path or ""
        if q.mode == "valor":
            self.mode.setCurrentIndex(1)
            k = [t[0] for t in self._types].index(q.ptype) if q.ptype in donor.PASSIVE_TYPES else 0
            self.vtype.setCurrentIndex(k)
            self.value.setText(donor.fmt_value(q.val, q.ptype).replace("µ", "u").replace("Ω", ""))
            self.pkg.setText(q.pkg or "")
        else:
            self.mode.setCurrentIndex(0)
            self.code.setText(q.code)
        self.head.setText(f"<b>{_esc(q.label)}</b>" if q.label else "Procurar componente")
        if q.mode == "codigo" and not q.code:
            self.head.setText(self.head.text() + "<br><span style='color:#e3b341'>A descrição deste componente não "
                                                 "tem um código de fabricante. Digite o código (do esquema ou da "
                                                 "peça) e aperte Enter.</span>")
        self.show()
        self.raise_()
        self.activateWindow()
        self.run()

    def _mode_changed(self, i):
        self.fields.setCurrentIndex(i)
        self.in_pdfs.setEnabled(i == 0)

    def _query(self):
        if self.mode.currentIndex() == 0:
            return donor.Query("codigo", code=self.code.text().strip())
        ptype = self._types[self.vtype.currentIndex()][0]
        txt = self.value.text().strip().replace(" ", "")
        pv = donor.parse_value(txt)
        if pv is not None and ptype == "ResistorColor" and txt in ("0", "0R", "0r"):
            pv = (0.0, "R")
        return donor.Query("valor", ptype=ptype, val=pv[0] if pv else None, pkg=self.pkg.text().strip().upper())

    # ------------------------------------------------------------------ busca
    def run(self):
        q = self._query()
        self.tree.clear()
        if not self.roots_fn():
            self.summary.setText("")
            self._message("Nenhuma pasta na biblioteca. Clique em “Pastas da biblioteca…” e escolha a pasta onde "
                          "ficam seus boardviews e esquemas.")
            return
        if not q.ok():
            self.summary.setText("Digite o que procurar." if q.mode == "codigo" or self.value.text().strip()
                                 else "Digite o valor.")
            if q.mode == "valor" and self.value.text().strip():
                self.summary.setText(f"Não entendi o valor “{self.value.text().strip()}” (ex.: 22uF, 10k, 4R7).")
            return
        self.summary.setText("procurando…")
        self._gen += 1
        gen = self._gen
        db = self.index.db_path
        boards, pdfs = self.in_boards.isChecked(), self.in_pdfs.isChecked() and q.mode == "codigo"
        origin = self.origin

        def work():
            try:
                con = donor.connect(db)
                try:
                    res = donor.search(con, q, boards=boards, pdfs=pdfs, exclude=[origin])
                finally:
                    con.close()
            except Exception as e:  # noqa
                res = e
            self._searcher.done.emit(gen, (q, res))
        threading.Thread(target=work, daemon=True).start()

    def _message(self, text):
        it = QTreeWidgetItem([text, "", ""])
        it.setFirstColumnSpanned(True)
        it.setForeground(0, QBrush(QColor("#9aa3af")))
        self.tree.addTopLevelItem(it)

    def _show(self, gen, payload):
        if gen != self._gen:
            return
        q, res = payload
        self.tree.clear()
        if isinstance(res, Exception):
            self.summary.setText(f"Erro na busca: {res}")
            return
        nb = sum(1 for h in res if h.kind == "board")
        npdf = len(res) - nb
        parts = sum(len(h.items) for h in res if h.kind == "board")
        if not res:
            self.summary.setText(f"Nada encontrado para {q.describe()}.")
            self._message("Nenhuma outra placa ou esquema da biblioteca tem este componente"
                          + (" (o índice ainda está sendo montado: tente de novo daqui a pouco)."
                             if self.index.running() else "."))
            return
        self.summary.setText(f"{q.describe()}: {nb} placa(s) com {parts} peça(s)"
                             + (f" · {npdf} esquema(s)" if npdf else ""))
        bold = QFont()
        bold.setBold(True)
        for h in res:
            name = os.path.basename(h.path)
            folder = os.path.basename(os.path.dirname(h.path))
            n = len(h.items)
            if h.kind == "board":
                top = QTreeWidgetItem([name, f"{n} peça{'s' if n != 1 else ''}", folder])
                for pname, mfg, side, sc in h.items:
                    tag = {3: "", 2: "  (variante)", 1: "  (parecido)"}.get(sc, "") if isinstance(sc, int) else ""
                    ch = QTreeWidgetItem([f"   {pname}", f"{SIDE.get(side, '')}{tag}", mfg])
                    ch.setData(0, Qt.UserRole, ("board", h.path, pname))
                    if tag:
                        ch.setForeground(1, QBrush(QColor("#e3b341")))
                    top.addChild(ch)
                top.setData(0, Qt.UserRole, ("board", h.path, h.items[0][0] if h.items else ""))
            else:
                top = QTreeWidgetItem([name, f"{n} página{'s' if n != 1 else ''} (esquema)", folder])
                top.setForeground(0, QBrush(QColor("#7fb3ff")))
                for page, snips, term in h.items:
                    l, m, r = snips[0]
                    ch = QTreeWidgetItem([f"   página {page + 1}", m, f"…{l[-40:]}{m}{r[:40]}…"])
                    ch.setData(0, Qt.UserRole, ("pdf", h.path, term, page))
                    top.addChild(ch)
                top.setData(0, Qt.UserRole, ("pdf", h.path, h.items[0][2], h.items[0][0]))
            top.setFont(0, bold)
            top.setToolTip(0, h.path)
            self.tree.addTopLevelItem(top)
        if len(res) <= 3:
            self.tree.expandAll()

    def _open(self, it, _col=0):
        d = it.data(0, Qt.UserRole)
        if not d:
            return
        if d[0] == "board":
            self.openBoard.emit(d[1], d[2])
        else:
            self.openPdf.emit(d[1], d[2], int(d[3]))

    # ------------------------------------------------------------------ índice
    def _progress(self, k, n, path):
        if n and k < n:
            self.status.setText(f"Atualizando o índice: {k + 1} de {n} — {os.path.basename(path)}")
        else:
            self._update_status()

    def _index_done(self, b, p):
        self._update_status(b, p)
        if self.isVisible() and (self.code.text().strip() or self.value.text().strip()):
            self.run()

    def _update_status(self, b=None, p=None):
        if b is None:
            try:
                con = donor.connect(self.index.db_path)
                b, p = donor.stats(con)
                con.close()
            except Exception:  # noqa
                b = p = 0
        extra = "  (atualizando…)" if self.index.running() else ""
        self.status.setText(f"Índice: {b} boardviews e {p} esquemas em PDF{extra}")


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
