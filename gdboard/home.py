"""Tela de início (aparece em toda aba vazia): arquivos recentes com miniatura e a biblioteca de boardviews."""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QSize, Signal, QObject, QThread, QTimer
from PySide6.QtGui import QIcon, QPixmap, QColor, QFont
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem,
                               QLineEdit, QTreeWidget, QTreeWidgetItem, QFileDialog, QMenu, QAbstractItemView,
                               QFrame, QSplitter, QMessageBox, QInputDialog)

from .library import LibraryIndex, thumb_path, fmt_size, fmt_age


class _Scanner(QObject):
    done = Signal(list)

    def __init__(self, roots):
        super().__init__()
        self.roots = roots

    def run(self):
        self.done.emit(LibraryIndex.scan(self.roots))


class HomePage(QWidget):
    openFile = Signal(str, str)        # caminho do boardview, PDF sugerido ("" = nenhum)
    openDialog = Signal()
    showTips = Signal()

    def __init__(self, win, data_dir, app_name, version):
        super().__init__()
        self.win = win
        self.data_dir = data_dir
        self.index = LibraryIndex(os.path.join(data_dir, "biblioteca.json"))
        self._thread = None
        self.setObjectName("Home")
        self.setStyleSheet("""
            #Home { background: #14171c; }
            QLabel#H1 { font-size: 22px; font-weight: 700; color: #e8e8ea; }
            QLabel#Sub { color: #7d8696; }
            QLabel#Sec { font-size: 14px; font-weight: 700; color: #e3b341; padding-top: 6px; }
            QPushButton#Big { padding: 8px 16px; font-weight: 600; }
            QListWidget, QTreeWidget { background: #1a1e25; border: 1px solid #262b34; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)

        # cabeçalho
        head = QHBoxLayout()
        logo = QLabel()
        try:
            from .icon_data import app_icon
            logo.setPixmap(app_icon().pixmap(64, 64))
        except Exception:  # noqa
            pass
        head.addWidget(logo)
        tl = QVBoxLayout()
        h1 = QLabel(app_name)
        h1.setObjectName("H1")
        sub = QLabel(f"versão {version} · criado por GDTech Informática")
        sub.setObjectName("Sub")
        tl.addWidget(h1)
        tl.addWidget(sub)
        head.addLayout(tl, 1)
        for txt, fn in (("Abrir boardview…", self.openDialog.emit), ("Dicas", self.showTips.emit)):
            b = QPushButton(txt)
            b.setObjectName("Big")
            b.clicked.connect(fn)
            head.addWidget(b)
        root.addLayout(head)
        hint = QLabel("Arraste boardviews (e o PDF do esquema junto) para a janela. Cada arquivo abre numa aba.")
        hint.setObjectName("Sub")
        root.addWidget(hint)

        split = QSplitter(Qt.Horizontal)
        # ---- recentes
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 8, 0)
        sec = QLabel("Recentes")
        sec.setObjectName("Sec")
        lv.addWidget(sec)
        self.recent = QListWidget()
        self.recent.setViewMode(QListWidget.IconMode)
        self.recent.setIconSize(QSize(208, 130))
        self.recent.setGridSize(QSize(232, 190))
        self.recent.setResizeMode(QListWidget.Adjust)
        self.recent.setMovement(QListWidget.Static)
        self.recent.setWordWrap(True)
        self.recent.setSpacing(4)
        self.recent.itemActivated.connect(self._open_recent)
        self.recent.itemClicked.connect(self._open_recent)
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent.customContextMenuRequested.connect(self._recent_menu)
        lv.addWidget(self.recent, 1)
        split.addWidget(left)

        # ---- biblioteca
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(8, 0, 0, 0)
        row = QHBoxLayout()
        sec2 = QLabel("Biblioteca")
        sec2.setObjectName("Sec")
        row.addWidget(sec2)
        row.addStretch(1)
        b_add = QPushButton("Adicionar pasta…")
        b_add.clicked.connect(self.add_folder)
        b_mng = QPushButton("Pastas…")
        b_mng.clicked.connect(self._folders_menu)
        b_ref = QPushButton("Atualizar")
        b_ref.clicked.connect(self.rescan)
        row.addWidget(b_add)
        row.addWidget(b_mng)
        row.addWidget(b_ref)
        rv.addLayout(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Procurar na biblioteca… (ex.: 3080 msi, V389, 4090 tvw)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._fill_library)
        rv.addWidget(self.search)
        self.lib_lbl = QLabel("")
        self.lib_lbl.setObjectName("Sub")
        rv.addWidget(self.lib_lbl)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Arquivo", "Pasta", "Formato", "Tamanho", "Esquema"])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.itemActivated.connect(self._open_lib)
        self.tree.itemDoubleClicked.connect(self._open_lib)
        rv.addWidget(self.tree, 1)
        split.addWidget(right)
        split.setSizes([520, 680])
        root.addWidget(split, 1)
        self.refresh()
        # varre de novo se o índice tiver mais de 1 dia
        if self.roots() and time.time() - self.index.when > 86400:
            QTimer.singleShot(1500, self.rescan)

    # ------------------------------------------------------------------ recentes
    def refresh(self):
        self.recent.clear()
        rec = self.win.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        ph = QPixmap(208, 130)
        ph.fill(QColor("#1f242c"))
        for p in rec:
            th = thumb_path(self.data_dir, p)
            pm = QPixmap(th) if os.path.exists(th) else ph
            folder = os.path.basename(os.path.dirname(p))
            it = QListWidgetItem(QIcon(pm.scaled(208, 130, Qt.KeepAspectRatio, Qt.SmoothTransformation)),
                                 f"{os.path.basename(p)}\n{folder}")
            it.setToolTip(p + ("" if os.path.exists(p) else "\n(arquivo não encontrado)"))
            it.setData(Qt.UserRole, p)
            if not os.path.exists(p):
                it.setForeground(QColor("#7d5050"))
            self.recent.addItem(it)
        self._fill_library()

    def _open_recent(self, it):
        p = it.data(Qt.UserRole)
        if p:
            self.openFile.emit(p, "")

    def _recent_menu(self, pos):
        it = self.recent.itemAt(pos)
        if it is None:
            return
        m = QMenu(self)
        m.addAction("Abrir", lambda: self._open_recent(it))
        m.addAction("Remover dos recentes", lambda: self._remove_recent(it.data(Qt.UserRole)))
        m.exec(self.recent.mapToGlobal(pos))

    def _remove_recent(self, path):
        rec = self.win.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        self.win.settings.setValue("recent", [r for r in rec if r != path])
        self.refresh()

    # ------------------------------------------------------------------ biblioteca
    def roots(self):
        v = self.win.settings.value("library/dirs", []) or []
        return [v] if isinstance(v, str) else list(v)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta com boardviews (e esquemas em PDF)",
                                             self.win.settings.value("last_dir", ""))
        if not d:
            return
        r = self.roots()
        if d not in r:
            r.append(d)
            self.win.settings.setValue("library/dirs", r)
        self.rescan()

    def _folders_menu(self):
        m = QMenu(self)
        r = self.roots()
        if not r:
            m.addAction("(nenhuma pasta — use “Adicionar pasta…”)").setEnabled(False)
        for d in r:
            sub = m.addMenu(d)
            sub.addAction("Remover da biblioteca", lambda d=d: self._remove_folder(d))
        m.exec(self.cursor().pos())

    def _remove_folder(self, d):
        self.win.settings.setValue("library/dirs", [x for x in self.roots() if x != d])
        self.rescan()

    def rescan(self):
        if self._thread is not None:
            return
        roots = [r for r in self.roots() if os.path.isdir(r)]
        if not roots:
            self.index.items, self.index.roots = [], []
            self.index.save()
            self._fill_library()
            return
        self.lib_lbl.setText("Procurando boardviews nas pastas…")
        self._thread = QThread(self)
        self._scanner = _Scanner(roots)
        self._scanner.moveToThread(self._thread)
        self._thread.started.connect(self._scanner.run)
        self._scanner.done.connect(self._scanned)
        self._scanner.done.connect(self._thread.quit)
        self._thread.finished.connect(self._scan_finished)
        self._thread.start()

    def _scanned(self, items):
        self.index.items = items
        self.index.roots = self.roots()
        self.index.when = time.time()
        self.index.save()
        self._fill_library()
        reindex = getattr(self.win, "_donor_auto", None)   # pastas novas: o índice de peças acompanha
        if reindex is not None:
            reindex()

    def _scan_finished(self):
        self._thread.deleteLater()
        self._thread = None

    def _fill_library(self):
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        roots = self.roots()
        if not roots:
            self.lib_lbl.setText("Nenhuma pasta na biblioteca. Clique em “Adicionar pasta…” e escolha a pasta onde "
                                 "ficam seus boardviews e esquemas — o programa acha tudo, inclusive o PDF de cada placa.")
            self.tree.setSortingEnabled(True)
            return
        res = self.index.search(self.search.text())
        n, with_pdf = self.index.stats()
        shown = f" · mostrando {len(res)}" if self.search.text().strip() else ""
        self.lib_lbl.setText(f"{len(roots)} pasta(s) · {n} boardviews · {with_pdf} com esquema em PDF{shown}")
        items = []
        for it in res:
            rel = os.path.relpath(os.path.dirname(it["path"]), it.get("root") or os.path.dirname(it["path"]))
            row = QTreeWidgetItem([it["name"], "" if rel == "." else rel, it["ext"].lstrip(".").upper(),
                                   fmt_size(it["size"]), "✓" if it.get("pdf") else ""])
            row.setToolTip(0, it["path"])
            if it.get("pdf"):
                row.setToolTip(4, it["pdf"])
            row.setData(0, Qt.UserRole, (it["path"], it.get("pdf", "")))
            items.append(row)
        self.tree.addTopLevelItems(items)
        self.tree.setSortingEnabled(True)
        for k in (0, 1):
            self.tree.resizeColumnToContents(k)

    def _open_lib(self, it, _col=0):
        path, pdf = it.data(0, Qt.UserRole)
        self.openFile.emit(path, pdf or "")
