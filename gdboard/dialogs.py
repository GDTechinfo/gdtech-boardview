"""Janelas de configuração: editor de tema, editor de atalhos e preferências."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal, QSize, QStandardPaths
from PySide6.QtGui import QColor, QKeySequence, QIcon, QPixmap, QPainter
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
                               QCheckBox, QColorDialog, QDialogButtonBox, QFileDialog, QMessageBox,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QComboBox, QSlider, QSpinBox, QGroupBox, QFormLayout, QScrollArea,
                               QWidget, QInputDialog, QToolButton, QLineEdit)

from . import keys as K
from .theme import Theme, FIELDS, BUILTIN, color_str


def user_dir(sub):
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    d = os.path.join(base, sub)
    os.makedirs(d, exist_ok=True)
    return d


def swatch(c, w=46, h=18):
    pm = QPixmap(w, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    # xadrez para mostrar transparência
    for y in range(0, h, 6):
        for x in range(0, w, 6):
            p.fillRect(x, y, 6, 6, QColor(90, 90, 90) if (x // 6 + y // 6) % 2 else QColor(140, 140, 140))
    if c is not None:
        p.fillRect(0, 0, w, h, QColor(*c))
    else:
        p.setPen(QColor(220, 60, 60))
        p.drawLine(0, h, w, 0)
    p.setPen(QColor(0, 0, 0))
    p.drawRect(0, 0, w - 1, h - 1)
    p.end()
    return QIcon(pm)


# ====================================================================== tema

class ThemeEditor(QDialog):
    themeChanged = Signal(object)

    def __init__(self, theme: Theme, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Editor de tema")
        self.setModal(False)
        self.theme = theme.copy()
        self.original = theme.copy()
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.name_lbl = QLabel()
        self.name_lbl.setStyleSheet("font-weight:600;")
        top.addWidget(self.name_lbl, 1)
        self.colorize = QCheckBox("Colorir componentes por tipo")
        self.colorize.toggled.connect(self._colorize)
        top.addWidget(self.colorize)
        lay.addLayout(top)

        area = QScrollArea()
        area.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        grid.setColumnStretch(0, 1)
        self.buttons = {}
        self.none_boxes = {}
        for row, (key, label, can_na) in enumerate(FIELDS):
            grid.addWidget(QLabel(label), row, 0)
            btn = QPushButton()
            btn.setIconSize(QSize(46, 18))
            btn.setFixedWidth(120)
            btn.clicked.connect(lambda _=False, k=key: self._pick(k))
            grid.addWidget(btn, row, 1)
            self.buttons[key] = btn
            if can_na:
                cb = QCheckBox("nenhuma")
                cb.toggled.connect(lambda on, k=key: self._set_none(k, on))
                grid.addWidget(cb, row, 2)
                self.none_boxes[key] = cb
        area.setWidget(inner)
        lay.addWidget(area, 1)

        row = QHBoxLayout()
        for text, fn in (("Importar .theme…", self._import), ("Exportar .theme…", self._export),
                         ("Salvar na lista…", self._save_list), ("Desfazer alterações", self._revert)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Fechar")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(520, 680)
        self._refresh()

    def _refresh(self):
        self.name_lbl.setText(f"Tema: {self.theme.name}")
        self.colorize.blockSignals(True)
        self.colorize.setChecked(self.theme.colorize)
        self.colorize.blockSignals(False)
        for key, btn in self.buttons.items():
            c = self.theme.colors.get(key)
            btn.setIcon(swatch(c))
            btn.setText(" " + (color_str(c)[:7] if c else "—"))
            if key in self.none_boxes:
                cb = self.none_boxes[key]
                cb.blockSignals(True)
                cb.setChecked(c is None)
                cb.blockSignals(False)

    def _emit(self):
        self._refresh()
        self.themeChanged.emit(self.theme)

    def _pick(self, key):
        c = self.theme.colors.get(key) or (128, 128, 128, 255)
        dlg = QColorDialog(QColor(*c), self)
        dlg.setOption(QColorDialog.ShowAlphaChannel, True)
        dlg.setWindowTitle(dict((k, l) for k, l, _ in FIELDS)[key])
        dlg.currentColorChanged.connect(lambda q: self._set(key, q))
        if dlg.exec() != QDialog.Accepted:
            self._set(key, QColor(*c))
        else:
            self._set(key, dlg.selectedColor())

    def _set(self, key, q):
        self.theme.colors[key] = (q.red(), q.green(), q.blue(), q.alpha())
        self._emit()

    def _set_none(self, key, on):
        if on:
            self.theme.colors[key] = None
        elif self.theme.colors.get(key) is None:
            self.theme.colors[key] = (128, 128, 128, 255)
        self._emit()

    def _colorize(self, on):
        self.theme.colorize = on
        self._emit()

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar tema", "", "Tema (*.theme);;Todos (*)")
        if path:
            try:
                self.theme = Theme.load(path)
            except Exception as e:
                QMessageBox.warning(self, "Tema", f"Não foi possível ler o tema:\n{e}")
                return
            self._emit()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar tema", self.theme.name + ".theme",
                                              "Tema (*.theme)")
        if path:
            self.theme.save(path)

    def _save_list(self):
        name, ok = QInputDialog.getText(self, "Salvar tema", "Nome do tema:", text=self.theme.name)
        if ok and name.strip():
            self.theme.name = name.strip()
            self.theme.save(os.path.join(user_dir("themes"), self.theme.name + ".theme"))
            self._emit()

    def _revert(self):
        self.theme = self.original.copy()
        self._emit()


def list_user_themes():
    d = user_dir("themes")
    out = []
    for f in sorted(os.listdir(d)):
        if f.lower().endswith(".theme"):
            out.append(os.path.join(d, f))
    return out


# ====================================================================== atalhos

class KeyCapture(QPushButton):
    """Botão que grava a próxima tecla (ou botão do mouse) pressionada."""
    changed = Signal()

    def __init__(self, value="", parent=None):
        super().__init__(parent)
        self.value = value
        self.capturing = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._start)
        self._show()

    def _show(self):
        if self.capturing:
            self.setText("pressione uma tecla…")
        else:
            self.setText(self.value or "—")

    def _start(self):
        self.capturing = True
        self._show()
        self.grabKeyboard()

    def _stop(self):
        self.capturing = False
        self.releaseKeyboard()
        self._show()
        self.changed.emit()

    def keyPressEvent(self, e):
        if not self.capturing:
            return super().keyPressEvent(e)
        k = e.key()
        if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_unknown):
            return
        if k in (Qt.Key_Backspace, Qt.Key_Delete) and e.modifiers() == Qt.NoModifier:
            self.value = ""
        else:
            from PySide6.QtCore import QKeyCombination
            mods = e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)
            # Shift + símbolo (ex.: '+') já vem como o próprio símbolo
            if not (Qt.Key_Exclam <= k <= Qt.Key_AsciiTilde and not (Qt.Key_A <= k <= Qt.Key_Z)
                    and not (Qt.Key_0 <= k <= Qt.Key_9)):
                pass
            else:
                mods = mods & ~Qt.ShiftModifier
            seq = QKeySequence(QKeyCombination(mods, Qt.Key(k)))
            self.value = seq.toString(QKeySequence.PortableText)
        self._stop()

    def mousePressEvent(self, e):
        if self.capturing:
            names = {Qt.MiddleButton: "Middle Mouse Button", Qt.RightButton: "Right Mouse Button",
                     Qt.BackButton: "Mouse Button 4", Qt.ForwardButton: "Mouse Button 5"}
            if e.button() in names:
                self.value = names[e.button()]
                self._stop()
                return
            if e.button() == Qt.LeftButton:
                self._stop()   # clique cancela
                return
        super().mousePressEvent(e)

    def focusOutEvent(self, e):
        if self.capturing:
            self._stop()
        super().focusOutEvent(e)


class KeybindingsEditor(QDialog):
    def __init__(self, kb: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Atalhos de teclado e mouse")
        self.kb = {k: list(v) for k, v in kb.items()}
        lay = QVBoxLayout(self)
        info = QLabel("Clique em um atalho e pressione a nova tecla (ou o botão do meio/direito do mouse). "
                      "Backspace apaga. Conflitos aparecem em vermelho.")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.table = QTableWidget(len(K.ACTIONS), 3)
        self.table.setHorizontalHeaderLabels(["Ação", "Principal", "Alternativo"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.caps = {}
        for r, (aid, label, _, _) in enumerate(K.ACTIONS):
            it = QTableWidgetItem(label)
            it.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, it)
            for c in (0, 1):
                cap = KeyCapture(self.kb[aid][c])
                cap.changed.connect(lambda a=aid, cc=c, w=cap: self._changed(a, cc, w))
                self.table.setCellWidget(r, 1 + c, cap)
                self.caps[(aid, c)] = cap
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 170)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, fn in (("Importar .keybindings…", self._import), ("Exportar…", self._export),
                         ("Restaurar padrão", self._defaults)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.resize(640, 640)
        self._check()

    def _changed(self, aid, c, w):
        self.kb[aid][c] = w.value
        self._check()

    def _reload(self):
        for (aid, c), cap in self.caps.items():
            cap.value = self.kb[aid][c]
            cap._show()
        self._check()

    def _check(self):
        seen = {}
        for aid in K.ACTION_IDS:
            for v in self.kb[aid]:
                if v:
                    seen.setdefault(v.lower(), []).append(aid)
        for (aid, c), cap in self.caps.items():
            v = self.kb[aid][c]
            dup = v and len(seen.get(v.lower(), [])) > 1
            bad = v == "Left Mouse Button"
            cap.setStyleSheet("color:#ff6b6b; font-weight:600;" if (dup or bad) else "")
            cap.setToolTip("Conflito com: " + ", ".join(K.LABELS[a] for a in seen[v.lower()] if a != aid)
                           if dup else ("O botão esquerdo é usado para selecionar" if bad else ""))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar atalhos", "", "Atalhos (*.keybindings);;Todos (*)")
        if path:
            try:
                self.kb = K.load(path)
            except Exception as e:
                QMessageBox.warning(self, "Atalhos", f"Não foi possível ler o arquivo:\n{e}")
                return
            self._reload()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Exportar atalhos", "meus.keybindings", "Atalhos (*.keybindings)")
        if path:
            K.save(self.kb, path)

    def _defaults(self):
        self.kb = K.defaults()
        self._reload()


# ====================================================================== preferências

class PreferencesDialog(QDialog):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferências")
        self.prefs = dict(prefs)
        lay = QVBoxLayout(self)

        g1 = QGroupBox("Renderização")
        f1 = QFormLayout(g1)
        self.renderer = QComboBox()
        self.renderer.addItems(["GPU (OpenGL) — recomendado", "CPU (compatibilidade)"])
        self.renderer.setCurrentIndex(0 if prefs["renderer"] == "gpu" else 1)
        self.vsync = QCheckBox("Sincronizar com o monitor (VSync)")
        self.vsync.setChecked(prefs["vsync"])
        self.msaa = QComboBox()
        self.msaa.addItems(["Desligado", "2x", "4x", "8x"])
        self.msaa.setCurrentIndex({0: 0, 2: 1, 4: 2, 8: 3}.get(prefs["msaa"], 2))
        f1.addRow("Renderizador:", self.renderer)
        f1.addRow("", self.vsync)
        f1.addRow("Antisserrilhamento:", self.msaa)
        f1.addRow("", QLabel("<i>Mudanças aqui valem ao reiniciar o programa.</i>"))
        lay.addWidget(g1)

        g2 = QGroupBox("Movimento")
        f2 = QFormLayout(g2)
        self.smooth = QCheckBox("Zoom e movimento suaves (animados)")
        self.smooth.setChecked(prefs["smooth"])
        self.speed = self._slider(4, 40, int(prefs["smooth_speed"]))
        self.wheel = self._slider(25, 300, int(prefs["wheel_step"] * 100))
        f2.addRow("", self.smooth)
        f2.addRow("Rapidez da animação:", self.speed)
        f2.addRow("Sensibilidade da roda (%):", self.wheel)
        lay.addWidget(g2)

        g3 = QGroupBox("Aparência")
        f3 = QFormLayout(g3)
        self.pin_scale = self._spin(30, 300, int(prefs["pin_scale"] * 100))
        self.part_lbl = self._spin(50, 300, int(prefs["part_label_scale"] * 100))
        self.pin_lbl = self._spin(50, 300, int(prefs["pin_label_scale"] * 100))
        self.square = QComboBox()
        self.square.addItems(["Redondos", "Quadrados"])
        self.square.setCurrentIndex(1 if prefs["square_pins"] else 0)
        self.flip_axis = QComboBox()
        self.flip_axis.addItems(["Espelhar na horizontal (eixo Y)", "Espelhar na vertical (eixo X)"])
        self.flip_axis.setCurrentIndex(1 if prefs["flip_axis"] == "x" else 0)
        self.fps = QCheckBox("Mostrar FPS (renderiza continuamente)")
        self.fps.setChecked(prefs["show_fps"])
        self.hover = QCheckBox("Caixa de informações ao passar o mouse")
        self.hover.setChecked(prefs.get("hover_info", True))
        self.reopen = QCheckBox("Reabrir as placas da última vez (o programa continua abrindo na tela de Início)")
        self.reopen.setChecked(prefs.get("reopen_last_tabs", False))
        self.donor_auto = QCheckBox("Indexar a biblioteca em segundo plano (busca de peça em outras placas)")
        self.donor_auto.setChecked(prefs.get("donor_auto_index", True))
        self.gnd_subtle = QCheckBox("GND selecionado fica discreto (só o contorno dos pinos)")
        self.gnd_subtle.setChecked(prefs.get("gnd_subtle", True))
        self.ui_scale = QComboBox()
        self._scales = [100, 125, 150, 175, 200]
        self.ui_scale.addItems([f"{v} %" for v in self._scales])
        cur = int(prefs.get("ui_scale", 100) or 100)
        self.ui_scale.setCurrentIndex(self._scales.index(cur) if cur in self._scales else 0)
        self.cross = QCheckBox("Vista dupla: linhas da rede ligando as duas placas")
        self.cross.setChecked(prefs.get("netweb_cross", True))
        f3.addRow("Tamanho dos pinos (%):", self.pin_scale)
        f3.addRow("Texto dos componentes (%):", self.part_lbl)
        f3.addRow("Texto dos pinos (%):", self.pin_lbl)
        f3.addRow("Formato dos pinos:", self.square)
        f3.addRow("Lado de baixo:", self.flip_axis)
        f3.addRow("Tamanho da interface:", self.ui_scale)
        f3.addRow("", self.reopen)
        f3.addRow("", self.gnd_subtle)
        f3.addRow("", self.donor_auto)
        f3.addRow("", self.hover)
        f3.addRow("", self.cross)
        f3.addRow("", self.fps)
        lay.addWidget(g3)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _slider(a, b, v):
        s = QSlider(Qt.Horizontal)
        s.setRange(a, b)
        s.setValue(v)
        return s

    @staticmethod
    def _spin(a, b, v):
        s = QSpinBox()
        s.setRange(a, b)
        s.setValue(v)
        s.setSuffix(" %")
        return s

    def _ok(self):
        p = self.prefs
        p["renderer"] = "gpu" if self.renderer.currentIndex() == 0 else "cpu"
        p["vsync"] = self.vsync.isChecked()
        p["msaa"] = [0, 2, 4, 8][self.msaa.currentIndex()]
        p["smooth"] = self.smooth.isChecked()
        p["smooth_speed"] = float(self.speed.value())
        p["wheel_step"] = self.wheel.value() / 100.0
        p["pin_scale"] = self.pin_scale.value() / 100.0
        p["part_label_scale"] = self.part_lbl.value() / 100.0
        p["pin_label_scale"] = self.pin_lbl.value() / 100.0
        p["square_pins"] = self.square.currentIndex() == 1
        p["flip_axis"] = "x" if self.flip_axis.currentIndex() == 1 else "y"
        p["show_fps"] = self.fps.isChecked()
        p["hover_info"] = self.hover.isChecked()
        p["netweb_cross"] = self.cross.isChecked()
        p["reopen_last_tabs"] = self.reopen.isChecked()
        p["donor_auto_index"] = self.donor_auto.isChecked()
        p["gnd_subtle"] = self.gnd_subtle.isChecked()
        p["ui_scale"] = self._scales[self.ui_scale.currentIndex()]
        self.accept()


DEFAULT_PREFS = {
    "renderer": "gpu", "vsync": True, "msaa": 4, "smooth": True, "smooth_speed": 16.0, "wheel_step": 1.0,
    "pin_scale": 1.0, "part_label_scale": 1.0, "pin_label_scale": 1.0, "square_pins": False,
    "flip_axis": "y", "show_fps": False,
    "show_part_labels": True, "show_pin_labels": True, "show_net_names": True, "show_netweb": True,
    "show_boxes": True, "hover_info": True, "dual_view": False, "netweb_cross": True,
    "gnd_subtle": True, "show_loose": True, "show_mech": True, "show_minimap": True, "reopen_last_tabs": False, "donor_auto_index": True, "ui_scale": 100,
    "tips_on_start": False,
}
