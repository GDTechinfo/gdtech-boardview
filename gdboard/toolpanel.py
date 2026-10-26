"""Painel "Ferramentas": Busca (valor/encapsulamento/coringa), Caçar curto (+ seguir a rede),
Notas e Comparar revisões."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (QWidget, QTabWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
                               QTreeWidgetItem, QCheckBox, QComboBox, QFileDialog, QMessageBox, QHeaderView,
                               QApplication, QAbstractItemView)

from . import tools
from .notes import TAG_COLORS, hexcolor
from .parsers import TOP, BOTTOM

SEARCH_COLOR = (255, 170, 0, 255)
SIDE_TXT = {TOP: "Top", BOTTOM: "Bottom"}

HELP_SEARCH = ("<b>Como buscar</b> (na caixa de busca da barra):<br>"
               "• <b>U5*</b>, <b>C1?2</b> — coringa (* = qualquer coisa, ? = um caractere)<br>"
               "• <b>0.47uF</b>, <b>470n</b>, <b>10k</b>, <b>4R7</b> — por valor (0.47uF = 470nF)<br>"
               "• <b>0402</b>, <b>pkg:QFN</b> — por encapsulamento<br>"
               "• nome exato (U7000, PP3V3) — vai direto nele")


def _tree(headers):
    t = QTreeWidget()
    t.setHeaderLabels(headers)
    t.setRootIsDecorated(False)
    t.setUniformRowHeights(True)
    t.setAlternatingRowColors(True)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.header().setStretchLastSection(True)
    return t


class ToolPanel(QTabWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._build_search()
        self._build_short()
        self._build_notes()
        self._build_compare()
        self.compare_rows = []

    # ------------------------------------------------------------------ atalhos
    @property
    def c(self):
        return self.win.canvas

    @property
    def b(self):
        return self.win.board

    def board_changed(self):
        """Aba trocada ou placa carregada: limpa o que era da outra placa."""
        d = self.win.current_doc()
        st = getattr(d, "search_state", None) if d else None
        self.search_tree.clear()
        self.search_lbl.setText("")
        if st:
            self._fill_search(*st)
        self.short_net = getattr(d, "short_net", None) if d else None
        self._refresh_short()
        self._refresh_notes()
        self._fill_compare_tabs()
        self.cmp_tree.clear()
        self.cmp_lbl.setText("")

    # ------------------------------------------------------------------ BUSCA
    def _build_search(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        self.search_lbl = QLabel("")
        self.search_lbl.setWordWrap(True)
        self.search_tree = _tree(["Componente", "Valor", "Lado", "Encaps."])
        self.search_tree.itemActivated.connect(self._search_go)
        self.search_tree.itemClicked.connect(self._search_go)
        row = QHBoxLayout()
        b1 = QPushButton("Enquadrar todos")
        b1.clicked.connect(self._search_fit)
        b2 = QPushButton("Limpar")
        b2.clicked.connect(self.clear_search)
        row.addWidget(b1)
        row.addWidget(b2)
        help_ = QLabel(HELP_SEARCH)
        help_.setWordWrap(True)
        help_.setStyleSheet("color: #8b94a3; font-size: 11px;")
        v.addWidget(self.search_lbl)
        v.addWidget(self.search_tree, 1)
        v.addLayout(row)
        v.addWidget(help_)
        self.addTab(w, "Busca")

    def run_search(self, query, kind=None):
        """Busca especial. Devolve True se tratou (coringa/valor/encapsulamento).
        kind="part" ou "net": só componentes ou só redes (caixas separadas da barra)."""
        b = self.b
        if b is None:
            return False
        desc, parts, nets = tools.search(b, self.c.ptype, query)
        if not desc:
            return False
        if kind == "part":
            nets = []
        elif kind == "net":
            parts = []
        d = self.win.current_doc()
        if d is not None:
            d.search_state = (desc, parts, nets)
        self._fill_search(desc, parts, nets)
        self.c.set_part_marks({pi: SEARCH_COLOR for pi in parts})
        self.c.set_group_nets(nets)
        self.setCurrentIndex(0)
        self.win.tools_dock.show()
        self.win.tools_dock.raise_()
        if parts:
            if len(parts) == 1:
                self.c.focus_part(parts[0])
            else:
                self.c.fit_parts(parts)
        elif len(nets) == 1:
            self.c.select_net(nets[0], zoom=True)
        return True

    def _fill_search(self, desc, parts, nets):
        b = self.b
        t = self.search_tree
        t.clear()
        if b is None:
            return
        n = len(parts) + len(nets)
        self.search_lbl.setText(f"<b>{n}</b> resultado(s) para {desc}" if n else f"Nada encontrado para {desc}")
        ptype = self.c.ptype
        items = []
        for pi in sorted(parts, key=lambda i: tools_natkey(b.parts[i].name)):
            p = b.parts[pi]
            it = QTreeWidgetItem([p.name, tools.value_of(p, ptype[pi]) or "", SIDE_TXT.get(p.side, "T/B"),
                                  tools.package_of(p) or ""])
            it.setData(0, Qt.UserRole, ("part", pi))
            items.append(it)
        for ni in sorted(nets, key=lambda i: tools_natkey(b.nets[i])):
            it = QTreeWidgetItem([b.nets[ni], "(rede)", "", f"{len(b.net_pins[ni])} pinos"])
            it.setData(0, Qt.UserRole, ("net", ni))
            items.append(it)
        t.addTopLevelItems(items)
        for k in range(3):
            t.resizeColumnToContents(k)

    def _search_go(self, it, _col=0):
        kind, i = it.data(0, Qt.UserRole)
        if kind == "part":
            self.c.focus_part(i)
        else:
            self.c.select_net(i, zoom=True)

    def _search_fit(self):
        parts = [it.data(0, Qt.UserRole)[1] for it in self._items(self.search_tree)
                 if it.data(0, Qt.UserRole)[0] == "part"]
        if parts:
            self.c.fit_parts(parts)

    def clear_search(self):
        self.search_tree.clear()
        self.search_lbl.setText("")
        d = self.win.current_doc()
        if d is not None:
            d.search_state = None
        if self.c is not None:
            self.c.set_part_marks({})
            self.c.set_group_nets(set())

    # ------------------------------------------------------------------ CAÇAR CURTO
    def _build_short(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        self.short_lbl = QLabel("Selecione um pino da rede em curto e clique em “Usar rede selecionada”.")
        self.short_lbl.setWordWrap(True)
        row = QHBoxLayout()
        b_use = QPushButton("Usar rede selecionada")
        b_use.clicked.connect(lambda: self.use_selected_net())
        self.short_follow = QCheckBox("Incluir redes ligadas (seguir a rede)")
        self.short_follow.setToolTip("Inclui também as redes ligadas por resistor 0 Ω, indutor, fusível, jumper…")
        self.short_follow.toggled.connect(lambda _on: self._refresh_short())
        row.addWidget(b_use)
        v.addWidget(self.short_lbl)
        v.addLayout(row)
        v.addWidget(self.short_follow)

        self.short_tabs = QTabWidget()
        self.caps_tree = _tree(["✓", "Capacitor", "Valor", "Lado", "Outra ponta"])
        self.caps_tree.itemActivated.connect(lambda it, _c=0: self._cap_go(self.caps_tree.indexOfTopLevelItem(it)))
        self.caps_tree.itemClicked.connect(self._cap_clicked)
        self.follow_tree = _tree(["Rede", "Ligada por", "Nível", "Pinos"])
        self.follow_tree.itemActivated.connect(self._follow_go)
        self.follow_tree.itemClicked.connect(self._follow_go)
        self.short_tabs.addTab(self.caps_tree, "Capacitores")
        self.short_tabs.addTab(self.follow_tree, "Seguir a rede")
        v.addWidget(self.short_tabs, 1)

        nav = QHBoxLayout()
        b_prev = QPushButton("◀ Anterior")
        b_next = QPushButton("Próximo ▶")
        b_ok = QPushButton("✓ Verificado + próximo")
        b_ok.setToolTip("Marca o capacitor atual como verificado e vai para o próximo (tecla V)")
        b_prev.clicked.connect(lambda: self.short_step(-1))
        b_next.clicked.connect(lambda: self.short_step(1))
        b_ok.clicked.connect(self.short_check_next)
        nav.addWidget(b_prev)
        nav.addWidget(b_next)
        nav.addWidget(b_ok)
        v.addLayout(nav)
        row2 = QHBoxLayout()
        self.short_status = QLabel("")
        b_reset = QPushButton("Zerar verificados")
        b_reset.clicked.connect(self._reset_checked)
        row2.addWidget(self.short_status, 1)
        row2.addWidget(b_reset)
        v.addLayout(row2)
        self.addTab(w, "Caçar curto")
        self.short_net = None
        self.short_rows = []
        self.short_cur = -1

    def use_selected_net(self, ni=None):
        c = self.c
        if ni is None:
            ni = c.sel_net
        if ni is None:
            QMessageBox.information(self, "Caçar curto", "Clique primeiro num pino da rede em curto (ex.: VCORE).")
            return
        self.short_net = int(ni)
        d = self.win.current_doc()
        if d is not None:
            d.short_net = self.short_net
        self.short_cur = -1
        self._refresh_short()
        self.setCurrentIndex(1)
        self.win.tools_dock.show()
        self.win.tools_dock.raise_()

    def follow_selected_net(self, ni=None):
        self.use_selected_net(ni)
        self.short_follow.setChecked(True)
        self.short_tabs.setCurrentIndex(1)

    def _refresh_short(self):
        b = self.b
        self.caps_tree.clear()
        self.follow_tree.clear()
        self.short_rows = []
        if b is None or self.short_net is None or self.short_net >= len(b.nets):
            self.short_lbl.setText("Selecione um pino da rede em curto e clique em “Usar rede selecionada”.")
            self.short_status.setText("")
            if self.c is not None:
                self.c.set_group_nets(set())
            return
        ni = self.short_net
        ptype = self.c.ptype
        fl = tools.follow_net(b, ptype, ni)
        nets = [n for n, *_ in fl] if self.short_follow.isChecked() else [ni]
        gnd = " (GND — não dá para caçar curto no próprio GND)" if b.net_is_gnd[ni] else ""
        self.short_lbl.setText(f"Rede: <b>{b.nets[ni]}</b>{gnd} · {len(b.net_pins[ni])} pinos"
                               + (f" · {len(fl) - 1} redes ligadas" if len(fl) > 1 else ""))
        # seguir a rede
        items = []
        for n, depth, pi, prev in fl:
            via = ""
            if pi is not None:
                p = b.parts[pi]
                val = tools.value_of(p, ptype[pi])
                via = p.name + (f" ({val})" if val else "")
            it = QTreeWidgetItem([b.nets[n], via, str(depth), str(len(b.net_pins[n]))])
            it.setData(0, Qt.UserRole, (n, pi))
            items.append(it)
        self.follow_tree.addTopLevelItems(items)
        for k in range(3):
            self.follow_tree.resizeColumnToContents(k)
        # capacitores
        store = self._store()
        self.short_rows = tools.caps_on_nets(b, ptype, nets)
        items = []
        for r in self.short_rows:
            it = QTreeWidgetItem(["", r["name"], r["value"], SIDE_TXT.get(r["side"], "T/B"),
                                  r["other"] + ("" if r["to_gnd"] else "  ⚠")])
            it.setCheckState(0, Qt.Checked if store and store.is_checked(r["name"]) else Qt.Unchecked)
            if not r["to_gnd"]:
                it.setToolTip(4, "Este capacitor não vai para o GND (liga duas redes)")
            items.append(it)
        self.caps_tree.addTopLevelItems(items)
        for k in range(4):
            self.caps_tree.resizeColumnToContents(k)
        self.c.set_group_nets(set(nets) if len(nets) > 1 else set())
        if self.c.sel_net != ni:
            self.c.select_net(ni)
        self._short_status()

    def _store(self):
        d = self.win.current_doc()
        return getattr(d, "notes", None) if d else None

    def _short_status(self):
        n = len(self.short_rows)
        if not n:
            self.short_status.setText("Nenhum capacitor nesta rede.")
            return
        store = self._store()
        done = sum(1 for r in self.short_rows if store and store.is_checked(r["name"]))
        top = sum(1 for r in self.short_rows if r["side"] == TOP)
        pos = f"{self.short_cur + 1} de {n}" if self.short_cur >= 0 else f"{n} capacitores"
        self.short_status.setText(f"{pos} · Top {top} / Bottom {n - top} · {done} verificados")

    def _cap_clicked(self, it, col):
        k = self.caps_tree.indexOfTopLevelItem(it)
        if col == 0:
            store = self._store()
            if store is not None:
                store.set_checked(self.short_rows[k]["name"], it.checkState(0) == Qt.Checked)
            self._short_status()
            return
        self._cap_go(k)

    def _cap_go(self, k):
        if not (0 <= k < len(self.short_rows)):
            return
        self.short_cur = k
        it = self.caps_tree.topLevelItem(k)
        self.caps_tree.setCurrentItem(it)
        self.caps_tree.scrollToItem(it)
        self.c.focus_part(self.short_rows[k]["pi"])
        if self.c.sel_net != self.short_net:
            self.c.sel_net = self.short_net
            self.c.selection_updated()
            self.c.request_frame()
        self._short_status()

    def short_step(self, d):
        n = len(self.short_rows)
        if not n:
            return
        store = self._store()
        k = self.short_cur
        for _ in range(n):            # pula os já verificados
            k = (k + d) % n
            if not (store and store.is_checked(self.short_rows[k]["name"])):
                break
        self._cap_go(k)

    def short_check_next(self):
        if not self.short_rows:
            return
        if 0 <= self.short_cur < len(self.short_rows):
            store = self._store()
            name = self.short_rows[self.short_cur]["name"]
            if store is not None:
                store.set_checked(name, True)
            self.caps_tree.topLevelItem(self.short_cur).setCheckState(0, Qt.Checked)
        self.short_step(1)

    def _reset_checked(self):
        store = self._store()
        if store is not None:
            for r in self.short_rows:
                store.checked.discard(r["name"].upper())
            store.save()
        self._refresh_short()

    def _follow_go(self, it, _col=0):
        n, pi = it.data(0, Qt.UserRole)
        if pi is not None:
            self.c.focus_part(pi)
            self.c.sel_net = n
            self.c.selection_updated()
            self.c.request_frame()
        else:
            self.c.select_net(n, zoom=True)

    # ------------------------------------------------------------------ NOTAS
    def _build_notes(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        lbl = QLabel("Botão direito num componente da placa para marcar (Trocado, Suspeito, Verificar, OK) "
                     "ou escrever uma nota. Fica salvo junto com esta placa.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #8b94a3;")
        self.notes_tree = _tree(["Componente", "Marcação", "Nota", "Data"])
        self.notes_tree.itemActivated.connect(self._note_go)
        self.notes_tree.itemClicked.connect(self._note_go)
        row = QHBoxLayout()
        b_edit = QPushButton("Editar…")
        b_del = QPushButton("Remover")
        b_edit.clicked.connect(self._note_edit)
        b_del.clicked.connect(self._note_del)
        row.addWidget(b_edit)
        row.addWidget(b_del)
        v.addWidget(lbl)
        v.addWidget(self.notes_tree, 1)
        v.addLayout(row)
        self.addTab(w, "Notas")

    def _refresh_notes(self):
        self.notes_tree.clear()
        store = self._store()
        b = self.b
        if store is None or b is None:
            return
        items = []
        for name, n in sorted(store.notes.items(), key=lambda kv: tools_natkey(kv[0])):
            pi = b.part_index.get(name)
            it = QTreeWidgetItem([b.parts[pi].name if pi is not None else name, n.get("tag", ""),
                                  n.get("text", ""), n.get("time", "")])
            col = TAG_COLORS.get(n.get("tag"))
            if col:
                it.setForeground(1, QBrush(QColor(hexcolor(col))))
            it.setData(0, Qt.UserRole, name)
            items.append(it)
        self.notes_tree.addTopLevelItems(items)
        for k in range(3):
            self.notes_tree.resizeColumnToContents(k)

    def _note_go(self, it, _col=0):
        pi = self.b.part_index.get(it.data(0, Qt.UserRole)) if self.b else None
        if pi is not None:
            self.c.focus_part(pi)

    def _note_edit(self):
        it = self.notes_tree.currentItem()
        if it is not None:
            self.win.edit_note(it.data(0, Qt.UserRole))

    def _note_del(self):
        it = self.notes_tree.currentItem()
        if it is not None:
            self.win.remove_note(it.data(0, Qt.UserRole))

    # ------------------------------------------------------------------ COMPARAR
    def _build_compare(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(4, 6, 4, 4)
        lbl = QLabel("Compara esta placa com outra revisão (outra aba aberta ou um arquivo).")
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color: #8b94a3;")
        row = QHBoxLayout()
        self.cmp_combo = QComboBox()
        self.cmp_combo.setMinimumWidth(160)
        b_file = QPushButton("Arquivo…")
        b_go = QPushButton("Comparar")
        b_file.clicked.connect(self._compare_file)
        b_go.clicked.connect(self._compare_tab)
        row.addWidget(self.cmp_combo, 1)
        row.addWidget(b_file)
        row.addWidget(b_go)
        self.cmp_mark = QCheckBox("Marcar as diferenças na placa")
        self.cmp_mark.setChecked(True)
        self.cmp_mark.toggled.connect(lambda _on: self._apply_compare_marks())
        self.cmp_lbl = QLabel("")
        self.cmp_lbl.setWordWrap(True)
        self.cmp_tree = QTreeWidget()
        self.cmp_tree.setHeaderLabels(["Diferença", "Detalhe"])
        self.cmp_tree.setUniformRowHeights(True)
        self.cmp_tree.itemActivated.connect(self._cmp_go)
        self.cmp_tree.itemClicked.connect(self._cmp_go)
        legend = QLabel("<span style='color:#ff5050'>■</span> só nesta placa &nbsp; "
                        "<span style='color:#ffc828'>■</span> valor/código &nbsp; "
                        "<span style='color:#c85aff'>■</span> ligações &nbsp; "
                        "<span style='color:#50aaff'>■</span> posição")
        v.addWidget(lbl)
        v.addLayout(row)
        v.addWidget(self.cmp_mark)
        v.addWidget(self.cmp_lbl)
        v.addWidget(self.cmp_tree, 1)
        v.addWidget(legend)
        self.addTab(w, "Comparar")

    def _fill_compare_tabs(self):
        self.cmp_combo.clear()
        cur = self.win.current_doc()
        for d in self.win.docs:
            if d is not cur and d.board is not None:
                self.cmp_combo.addItem(os.path.basename(d.path), d)

    def _compare_tab(self):
        d = self.cmp_combo.currentData()
        if d is None or d.board is None:
            QMessageBox.information(self, "Comparar", "Abra a outra revisão numa aba (ou use “Arquivo…”).")
            return
        self._compare_with(d.board, os.path.basename(d.path))

    def _compare_file(self):
        cur = self.win.current_doc()
        start = os.path.dirname(cur.path) if cur and cur.path else ""
        path, _ = QFileDialog.getOpenFileName(self, "Outra revisão da placa", start, "Boardview (*.*)")
        if not path:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            from .board import Board
            from . import parsers
            other = Board(parsers.load_file(path, self.win.keys()), path)
        except Exception as e:  # noqa
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Comparar", f"Não consegui abrir:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        self._compare_with(other, os.path.basename(path))

    def _compare_with(self, other, name):
        b = self.b
        if b is None:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rows = tools.compare_boards(b, other, self.c.ptype)
        finally:
            QApplication.restoreOverrideCursor()
        self.compare_rows = rows
        self.cmp_tree.clear()
        groups = {}
        for cat, nm, det, pi in rows:
            groups.setdefault(cat, []).append((nm, det, pi))
        for cat in ["Só nesta placa", "Só na outra", "Valor diferente", "Código diferente",
                    "Encapsulamento diferente", "Ligações diferentes", "Posição diferente",
                    "Rede só nesta placa", "Rede só na outra"]:
            lst = groups.get(cat)
            if not lst:
                continue
            top = QTreeWidgetItem([f"{cat} ({len(lst)})", ""])
            col = tools.CATEGORY_COLORS.get(cat)
            if col:
                top.setForeground(0, QBrush(QColor(*col[:3])))
            for nm, det, pi in sorted(lst, key=lambda x: tools_natkey(x[0]))[:5000]:
                ch = QTreeWidgetItem([nm, det])
                ch.setData(0, Qt.UserRole, (pi, nm, cat))
                top.addChild(ch)
            self.cmp_tree.addTopLevelItem(top)
        self.cmp_tree.resizeColumnToContents(0)
        n_parts = sum(1 for r in rows if not r[0].startswith("Rede"))
        self.cmp_lbl.setText(f"Comparado com <b>{name}</b>: {n_parts} diferenças em componentes, "
                             f"{len(rows) - n_parts} em redes." if rows else
                             f"Comparado com <b>{name}</b>: nenhuma diferença encontrada.")
        self._apply_compare_marks()

    def _apply_compare_marks(self):
        if self.c is None:
            return
        if not self.cmp_mark.isChecked():
            self.c.set_part_marks({})
            return
        marks = {}
        for cat, _nm, _det, pi in self.compare_rows:
            if pi is not None and cat in tools.CATEGORY_COLORS and pi not in marks:
                marks[pi] = tools.CATEGORY_COLORS[cat]
        self.c.set_part_marks(marks)

    def _cmp_go(self, it, _col=0):
        data = it.data(0, Qt.UserRole)
        if not data:
            return
        pi, nm, cat = data
        if pi is not None:
            self.c.focus_part(pi)
        elif cat.startswith("Rede") and self.b and nm in self.b.net_index:
            self.c.select_net(self.b.net_index[nm], zoom=True)

    @staticmethod
    def _items(tree):
        return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def tools_natkey(s):
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]
