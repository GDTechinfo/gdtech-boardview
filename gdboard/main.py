"""GDTech BoardView — janela principal."""
from __future__ import annotations

import html
import os
import re
import sys
import traceback

from PySide6.QtCore import (Qt, QThread, Signal, QObject, QSettings, QStringListModel, QSortFilterProxyModel, QUrl,
                            QTimer)
from PySide6.QtGui import QAction, QKeySequence, QIcon, QDesktopServices, QPalette, QColor, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QFileDialog, QMessageBox, QDockWidget, QWidget,
                               QVBoxLayout, QHBoxLayout, QLineEdit, QListView, QTabWidget, QLabel,
                               QTableWidget, QTableWidgetItem, QHeaderView, QCompleter, QToolBar,
                               QDialog, QFormLayout, QDialogButtonBox, QPlainTextEdit, QAbstractItemView,
                               QStatusBar, QStyle, QTabBar, QStackedWidget, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QMenu, QInputDialog)

from . import parsers
from .board import Board
from .parsers import TOP, BOTTOM, BOTH
from .view import BoardCanvas
from .glview import GLBoardCanvas, gl_available, default_format
from . import keys as K
from .theme import Theme, BUILTIN
from .dialogs import (ThemeEditor, KeybindingsEditor, PreferencesDialog, DEFAULT_PREFS, list_user_themes,
                      user_dir)

APP_NAME = "GDTech BoardView"
APP_VERSION = "3.3.1"
HOME_AFTER_RESTORE = "\0inicio"
ORG = "GDTech Informática"


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ---------------------------------------------------------------------- chaves

def parse_fz_key(text: str):
    vals = re.findall(r"0x[0-9a-fA-F]+|\d+", text)
    if not vals:
        return None
    return [int(v, 16) if v.lower().startswith("0x") else int(v) for v in vals]


def parse_xzz_key(text: str):
    t = text.strip().replace(" ", "")
    if not t:
        return None
    try:
        return int(t, 16) if not t.isdigit() or t.lower().startswith("0x") else int(t)
    except ValueError:
        return None


class KeysDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chaves de descriptografia")
        self.settings = settings
        lay = QVBoxLayout(self)
        info = QLabel("O programa já vem com as chaves padrão de .fz, .cae e .pcb (XZZ) — normalmente não é preciso "
                      "mexer aqui.\nSó preencha se tiver um arquivo cifrado com outra chave (formato do OpenBoardView: "
                      "valores 0x... separados por vírgula).")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        self.fz = QPlainTextEdit(settings.value("keys/fz", ""))
        self.cae = QPlainTextEdit(settings.value("keys/cae", ""))
        self.xzz = QLineEdit(settings.value("keys/xzz", ""))
        for w in (self.fz, self.cae):
            w.setFixedHeight(90)
            w.setPlaceholderText("0x12345678, 0x12345678, ... (44 valores)")
        self.xzz.setPlaceholderText("0x0123456789ABCDEF")
        form.addRow("Chave FZ:", self.fz)
        form.addRow("Chave CAE:", self.cae)
        form.addRow("Chave XZZ (.pcb):", self.xzz)
        lay.addLayout(form)
        self.status = QLabel()
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(560, 380)

    def save(self):
        problems = []
        fz = parse_fz_key(self.fz.toPlainText())
        if fz and not parsers.check_fz_key(fz, parsers.FZ_PARITY):
            problems.append("chave FZ não passou na verificação de paridade")
        cae = parse_fz_key(self.cae.toPlainText())
        if cae and not parsers.check_fz_key(cae, parsers.CAE_PARITY):
            problems.append("chave CAE não passou na verificação de paridade")
        xzz = parse_xzz_key(self.xzz.text())
        if self.xzz.text().strip() and not parsers.check_xzz_key(xzz):
            problems.append("chave XZZ não passou na verificação de paridade")
        if problems:
            r = QMessageBox.question(self, "Chaves", "Atenção: " + "; ".join(problems) +
                                     ".\n\nSalvar mesmo assim?")
            if r != QMessageBox.Yes:
                return
        self.settings.setValue("keys/fz", self.fz.toPlainText().strip())
        self.settings.setValue("keys/cae", self.cae.toPlainText().strip())
        self.settings.setValue("keys/xzz", self.xzz.text().strip())
        self.accept()


# ---------------------------------------------------------------------- carregamento em segundo plano

class Loader(QObject):
    done = Signal(object, str)    # board, erro
    finished = Signal()

    def __init__(self, path, keys):
        super().__init__()
        self.path = path
        self.keys = keys

    def run(self):
        try:
            raw = parsers.load_file(self.path, self.keys)
            if not raw.valid:
                raise parsers.FormatError("O arquivo não pôde ser interpretado.")
            if not raw.pins and not raw.parts:
                raise parsers.FormatError("Nenhum componente/pino encontrado no arquivo.")
            board = Board(raw, self.path)
            self.done.emit(board, "")
        except parsers.FormatError as e:
            self.done.emit(None, str(e))
        except Exception as e:  # noqa
            self.done.emit(None, f"Erro ao ler o arquivo: {e}\n\n{traceback.format_exc(limit=3)}")
        self.finished.emit()


# ---------------------------------------------------------------------- janela

class Doc:
    """Uma aba: um boardview aberto (com seu próprio canvas, câmera, seleção e PDF do esquema)."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.board = None
        self.path = ""
        self.pdf_path = ""
        self.parts_list: list[str] = []
        self.nets_list: list[str] = []
        self.notes = None            # NoteStore
        self.search_state = None
        self.short_net = None
        self.is_home = False         # a aba fixa "Início" (nunca recebe placa, não fecha)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG, APP_NAME)
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)
        self.board: Board | None = None
        self._thread = None
        self._info_mode = None
        self.prefs = load_prefs(self.settings)
        self.theme = self._load_current_theme()
        self.kb = K.from_settings(self.settings.value("keybindings", "") or "")
        self.theme_editor = None
        self._fps = 0.0

        self._ui_ready = False
        self.docs: list[Doc] = []
        self._queue: list[str] = []
        self._pending_pdf: dict[str, str] = {}      # boardview -> PDF (arrastados juntos / biblioteca)
        self._pending_select: dict[str, str] = {}   # boardview -> componente a selecionar ao abrir
        self.donor_index = None                     # índice "peça em outras placas"
        self.donor_win = None
        self._pending_state: dict[str, dict] = {}   # boardview -> câmera (abas da última vez)
        self._pending_current = None
        self._force_cpu = False
        self.pdf_win = None
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.tabbar = QTabBar()
        self.tabbar.setTabsClosable(True)
        self.tabbar.setMovable(True)
        self.tabbar.setDocumentMode(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setElideMode(Qt.ElideMiddle)
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setStyleSheet(
            "QTabBar::tab { padding: 5px 12px; max-width: 320px; background: #1b1f26; color: #9aa3b2;"
            " border: 1px solid #262b34; border-bottom: none; }"
            "QTabBar::tab:selected { background: #232832; color: #e6e6e6; }"
            "QTabBar::tab:hover { color: #e6e6e6; }")
        self.tabbar.currentChanged.connect(self._tab_changed)
        self.tabbar.tabCloseRequested.connect(self.close_tab)
        self.tabbar.tabMoved.connect(self._tab_moved)
        self.stack = QStackedWidget()
        cl.addWidget(self.tabbar)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.canvas = None
        self._new_doc(home=True)     # aba "Início" fixa: as placas abrem em abas ao lado dela

        self._build_actions()
        self._build_docks()
        self._build_status()
        from .home import HomePage
        self.home = HomePage(self, user_dir(""), APP_NAME, APP_VERSION)
        self.home.openFile.connect(self._open_with_pdf)
        self.home.openDialog.connect(self.open_dialog)
        self.home.showTips.connect(self.show_tips)
        self.stack.addWidget(self.home)
        self._apply_prefs()
        self._apply_keybindings()
        if self.prefs.get("dual_view"):
            self._set_dual(True, save=False)
        self._ui_ready = True
        self._tab_changed(self.tabbar.currentIndex())

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1400, 860)
        st = self.settings.value("state")
        if st:
            self.restoreState(st)

    # -------------------------------------------------------------- canvas (GPU/CPU)
    def _make_canvas(self, renderer):
        canvas = None
        if renderer == "gpu" and gl_available() and not self._force_cpu:
            try:
                canvas = GLBoardCanvas()
                canvas.glFailed.connect(lambda msg, c=canvas: self._gl_failed(msg, c))
            except Exception:
                traceback.print_exc()
                canvas = None
        if canvas is None:
            canvas = BoardCanvas()
        canvas.theme = self.theme
        cur = lambda c=canvas: c is self.canvas  # noqa: E731
        canvas.selectionChanged.connect(lambda c=canvas: self.on_selection() if c is self.canvas else None)
        canvas.cursorMoved.connect(lambda x, y, c=canvas: self.on_cursor(x, y) if c is self.canvas else None)
        canvas.viewChanged.connect(lambda c=canvas: self._view_changed() if c is self.canvas else None)
        canvas.fpsChanged.connect(lambda f, c=canvas: self._on_fps(f) if c is self.canvas else None)
        canvas.pinStepped.connect(lambda k, n, c=canvas: self.statusBar().showMessage(
            f"Pino {k} de {n} da rede {c.board.nets[c.sel_net]}  (Tab / Shift+Tab)", 4000)
            if c is self.canvas and c.sel_net is not None else None)
        canvas.contextRequested.connect(
            lambda pi, pin, pos, c=canvas: self._context_menu(pi, pin, pos) if c is self.canvas else None)
        canvas.itemDoubleClicked.connect(
            lambda kind, name, c=canvas: self._item_double_clicked(kind, name) if c is self.canvas else None)
        del cur
        hud = QLabel(canvas)
        hud.setAttribute(Qt.WA_TransparentForMouseEvents)
        hud.setStyleSheet("QLabel { background: rgba(20,24,31,200); color: #e3b341; border-radius: 6px;"
                          " padding: 4px 10px; font-weight: 600; }")
        hud.move(10, 10)
        canvas._hud = hud
        from .minimap import Minimap
        canvas._minimap = Minimap(canvas)
        canvas._minimap.set_enabled(self.prefs.get("show_minimap", True))
        return canvas

    def _gl_failed(self, msg, canvas=None):
        """OpenGL não funcionou nesta máquina: troca para o renderizador por CPU (em todas as abas)."""
        self._force_cpu = True
        for d in self.docs:
            if d.canvas.renderer_name != "GPU":
                continue
            old = d.canvas
            d.canvas = self._make_canvas("cpu")
            self.stack.insertWidget(self.stack.indexOf(old), d.canvas)
            self.stack.removeWidget(old)
            old.setParent(None)
            old.deleteLater()
            self._setup_canvas(d.canvas)
            if d.board is not None:
                d.canvas.set_board(d.board)
        self._tab_changed(self.tabbar.currentIndex())
        self.statusBar().showMessage(f"OpenGL indisponível ({msg}). Usando renderização por CPU.", 10000)

    def _setup_canvas(self, c):
        """Aplica preferências, tema, atalhos do mouse e vista dupla num canvas novo."""
        self._apply_prefs_to(c)
        c.mouse_bindings = getattr(self, "_mouse_bindings", {})
        c.set_dual(self.prefs.get("dual_view", False))

    # -------------------------------------------------------------- abas
    def _new_doc(self, home=False):
        c = self._make_canvas(self.prefs["renderer"])
        d = Doc(c)
        d.is_home = home
        self.docs.append(d)
        self.stack.addWidget(c)
        if self._ui_ready:
            self._setup_canvas(c)
        i = self.tabbar.addTab("⌂  Início" if home else "Abrindo…")
        if home:
            # sem botão de fechar
            for side in (QTabBar.RightSide, QTabBar.LeftSide):
                self.tabbar.setTabButton(i, side, None)
            self.tabbar.setTabToolTip(i, "Arquivos recentes e biblioteca (Ctrl+T)")
        self.tabbar.setCurrentIndex(i)
        if self.canvas is None:
            self._tab_changed(i)
        return d

    def home_index(self):
        for k, d in enumerate(self.docs):
            if d.is_home:
                return k
        return 0

    def go_home(self):
        self.tabbar.setCurrentIndex(self.home_index())

    def current_doc(self):
        i = self.tabbar.currentIndex()
        return self.docs[i] if 0 <= i < len(self.docs) else None

    def _tab_moved(self, a, b):
        self.docs.insert(b, self.docs.pop(a))
        k = self.home_index()
        if k != 0:                                   # o Início fica sempre na frente
            self.tabbar.moveTab(k, 0)

    def _tab_changed(self, i):
        if not (0 <= i < len(self.docs)):
            return
        d = self.docs[i]
        self.canvas = d.canvas
        self.board = d.board
        self.stack.setCurrentWidget(d.canvas)
        if not self._ui_ready:
            return
        if d.board is None:
            self.stack.setCurrentWidget(self.home)       # aba vazia: tela de início
            self.home.refresh()
        self.part_model.setStringList(d.parts_list)
        self.net_model.setStringList(d.nets_list)
        self.part_complete_model.setStringList(d.parts_list)
        self.net_complete_model.setStringList(d.nets_list)
        if d.board is not None:
            self.setWindowTitle(f"{os.path.basename(d.path)} — {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)
            self.st_file.setText("")
        self.on_selection()
        self._view_changed()
        self.tools.board_changed()
        if self.pdf_win is not None and self.pdf_win.isVisible():
            self.pdf_win.show_doc_pdf(d.pdf_path)
        d.canvas.setFocus()

    def close_tab(self, i=None):
        if i is None or isinstance(i, bool):
            i = self.tabbar.currentIndex()
        if not (0 <= i < len(self.docs)) or self.docs[i].is_home:
            return                                   # o Início não fecha
        d = self.docs.pop(i)
        self.stack.removeWidget(d.canvas)
        d.canvas.setParent(None)
        d.canvas.deleteLater()
        self.tabbar.removeTab(i)
        if not self.docs:
            self._new_doc()
        self._tab_changed(self.tabbar.currentIndex())

    def next_tab(self, step=1):
        n = self.tabbar.count()
        if n > 1:
            self.tabbar.setCurrentIndex((self.tabbar.currentIndex() + step) % n)

    def _update_minimaps(self):
        for d in self.docs:
            d.canvas._minimap.set_enabled(self.prefs.get("show_minimap", True))

    def _view_changed(self):
        self._update_hud()
        self._sync_layer_panel()

    # -------------------------------------------------------------- vista dupla
    def _set_dual(self, on, save=True):
        """Vista dupla: o outro lado da placa aparece espelhado logo acima, na mesma
        cena, como um livro aberto. As duas placas se movem juntas, como uma coisa só."""
        if save:
            self.prefs["dual_view"] = bool(on)
            save_prefs(self.settings, self.prefs)
        a = self.actions_by_id.get("ToggleDualView") if self._ui_ready else None
        if a is not None and a.isChecked() != bool(on):
            a.blockSignals(True)
            a.setChecked(bool(on))
            a.blockSignals(False)
        self._apply_prefs()
        for d in self.docs:
            d.canvas.set_dual(bool(on))
        self._update_hud()

    def toggle_dual(self, on):
        self._set_dual(on)

    # -------------------------------------------------------------- UI
    def _act(self, aid, fn, checkable=False, checked=False, icon=None):
        a = QAction(K.LABELS.get(aid, aid), self)
        a.setCheckable(checkable)
        if checkable:
            a.setChecked(checked)
            a.toggled.connect(fn)
        else:
            a.triggered.connect(fn)
        if icon is not None:
            a.setIcon(self.style().standardIcon(icon))
        a.setShortcutContext(Qt.WindowShortcut)
        self.addAction(a)
        self.actions_by_id[aid] = a
        return a

    def _build_actions(self):
        self.actions_by_id = {}
        c = lambda: self.canvas  # noqa: E731  (o canvas pode ser trocado em tempo de execução)
        A = self._act
        a_open = A("OpenFile", self.open_dialog, icon=QStyle.SP_DialogOpenButton)
        a_open.setText("Abrir...")
        A("Find", lambda: (self.search.setFocus(), self.search.selectAll()))
        A("FindNet", lambda: (self.net_search.setFocus(), self.net_search.selectAll()))
        A("ZoomIn", lambda: c().zoom_at(1.35))
        A("ZoomOut", lambda: c().zoom_at(1 / 1.35))
        step = lambda: 0.18 * min(c().width(), c().height())  # noqa: E731
        A("PanUp", lambda: c().pan_pixels(0, step()))
        A("PanDown", lambda: c().pan_pixels(0, -step()))
        A("PanLeft", lambda: c().pan_pixels(step(), 0))
        A("PanRight", lambda: c().pan_pixels(-step(), 0))
        A("RotateClockwise", lambda: c().rotate(1))
        A("RotateCounterclockwise", lambda: c().rotate(-1))
        A("FitBoardToScreen", lambda: c().fit())
        A("ZoomToSelection", lambda: c().zoom_to_selection())
        dual = lambda: self.prefs.get("dual_view", False)  # noqa: E731
        A("FlipBoardSide", lambda: c().flip_side("x" if dual() else self.prefs["flip_axis"]))
        A("FlipBoardSideOverXAxis", lambda: c().flip_side(
            "x" if dual() else ("x" if self.prefs["flip_axis"] == "y" else "y")))
        A("ToggleDualView", self.toggle_dual, True, self.prefs.get("dual_view", False))
        A("ToggleNetwebCross", lambda on: self._set_pref("netweb_cross", on), True,
          self.prefs.get("netweb_cross", True))
        A("MirrorBoardHorizontally", lambda: c().mirror(True))
        A("MirrorBoardVertically", lambda: c().mirror(False))
        A("ToggleNetweb", lambda on: self._set_pref("show_netweb", on), True, self.prefs["show_netweb"])
        A("TogglePinLabels", lambda on: self._set_pref("show_pin_labels", on), True, self.prefs["show_pin_labels"])
        A("TogglePartLabels", lambda on: self._set_pref("show_part_labels", on), True,
          self.prefs["show_part_labels"])
        A("ToggleNetNames", lambda on: self._set_pref("show_net_names", on), True, self.prefs["show_net_names"])
        A("ToggleFPS", lambda on: self._set_pref("show_fps", on), True, self.prefs["show_fps"])
        A("ToggleHoverInfo", lambda on: self._set_pref("hover_info", on), True, self.prefs["hover_info"])
        A("ClearAllSelections", lambda: (c().clear_selection(), self.tools.clear_search()))
        A("ShortUseNet", lambda: self.tools.use_selected_net())
        A("FollowNet", lambda: self.tools.follow_selected_net())
        A("DonorSearch", self.donor_search_selected)
        A("ShortNext", lambda: self.tools.short_step(1))
        A("ShortPrev", lambda: self.tools.short_step(-1))
        A("ShortCheckNext", lambda: self.tools.short_check_next())
        A("ToggleTools", lambda: (self.tools_dock.show(), self.tools_dock.raise_()))
        A("ToggleLayerMode", self.toggle_layer_mode, True, False)
        A("NextNetPin", lambda: c().step_net_pin(1))
        A("PrevNetPin", lambda: c().step_net_pin(-1))
        A("ToggleMinimap", lambda on: (self._set_pref("show_minimap", on), self._update_minimaps()), True,
          self.prefs["show_minimap"])
        A("ToggleLoose", lambda on: self._set_pref("show_loose", on), True, self.prefs["show_loose"])
        A("ToggleMech", lambda on: self._set_pref("show_mech", on), True, self.prefs.get("show_mech", True))
        A("ToggleGndSubtle", lambda on: self._set_pref("gnd_subtle", on), True, self.prefs["gnd_subtle"])
        A("NewTab", self.go_home)
        A("LayerNext", lambda: (c().step_layer(1), self._sync_layer_panel()))
        A("LayerPrev", lambda: (c().step_layer(-1), self._sync_layer_panel()))
        A("OpenSchematic", self.open_schematic)
        A("CloseTab", lambda: self.close_tab())
        A("NextTab", lambda: self.next_tab(1))
        A("PrevTab", lambda: self.next_tab(-1))
        self.a_boxes = QAction("Mostrar contornos dos componentes", self, checkable=True,
                               checked=self.prefs["show_boxes"])
        self.a_boxes.toggled.connect(lambda on: self._set_pref("show_boxes", on))

        mb = self.menuBar()
        ab = self.actions_by_id
        fm = mb.addMenu("&Arquivo")
        fm.addAction(a_open)
        fm.addAction(ab["OpenSchematic"])
        fm.addAction(ab["NewTab"])
        fm.addAction(ab["CloseTab"])
        self.recent_menu = fm.addMenu("Recentes")
        self._refresh_recent()
        fm.addSeparator()
        fm.addAction(QAction("Chaves de descriptografia...", self, triggered=self.edit_keys))
        fm.addSeparator()
        fm.addAction(QAction("Sair", self, shortcut=QKeySequence.Quit, triggered=self.close))

        vm = mb.addMenu("&Exibir")
        for aid in ("FlipBoardSide", "FlipBoardSideOverXAxis", "RotateClockwise", "RotateCounterclockwise",
                    "MirrorBoardHorizontally", "MirrorBoardVertically", None, "FitBoardToScreen",
                    "ZoomToSelection", "ZoomIn", "ZoomOut", None, "TogglePartLabels", "TogglePinLabels",
                    "ToggleNetNames", "ToggleNetweb"):
            vm.addSeparator() if aid is None else vm.addAction(ab[aid])
        vm.addAction(self.a_boxes)
        vm.addSeparator()
        vm.addAction(ab["ToggleMinimap"])
        vm.addAction(ab["ToggleLoose"])
        vm.addAction(ab["ToggleMech"])
        vm.addAction(ab["ToggleGndSubtle"])
        vm.addAction(ab["NextNetPin"])
        vm.addAction(ab["PrevNetPin"])
        vm.addSeparator()
        vm.addAction(ab["ToggleDualView"])
        vm.addAction(ab["ToggleNetwebCross"])
        vm.addSeparator()
        vm.addAction(ab["ToggleLayerMode"])
        vm.addAction(ab["LayerNext"])
        vm.addAction(ab["LayerPrev"])
        vm.addSeparator()
        vm.addAction(ab["ToggleTools"])
        vm.addSeparator()
        vm.addAction(ab["NextTab"])
        vm.addAction(ab["PrevTab"])
        vm.addSeparator()
        vm.addAction(ab["ToggleHoverInfo"])
        vm.addAction(ab["ToggleFPS"])
        vm.addAction(ab["ClearAllSelections"])

        self.theme_menu = mb.addMenu("&Tema")
        self.theme_menu.aboutToShow.connect(self._fill_theme_menu)

        tm = mb.addMenu("&Ferramentas")
        for aid in ("ShortUseNet", "FollowNet", "ShortNext", "ShortPrev", "ShortCheckNext"):
            tm.addAction(ab[aid])
        tm.addSeparator()
        tm.addAction(ab["DonorSearch"])
        tm.addSeparator()
        tm.addAction(QAction("Editor de tema...", self, triggered=self.edit_theme))
        tm.addAction(QAction("Atalhos de teclado e mouse...", self, triggered=self.edit_keybindings))
        tm.addSeparator()
        tm.addAction(QAction("Preferências...", self, shortcut="Ctrl+,", triggered=self.edit_prefs))

        hm = mb.addMenu("A&juda")
        hm.addAction(QAction("Dicas…", self, triggered=self.show_tips))
        hm.addAction(QAction("Atalhos atuais", self, triggered=self.show_shortcuts))
        # "Sobre" fica direto na barra, ao lado do Ajuda
        self.about_action = QAction("&Sobre", self, triggered=self.show_about)
        mb.addAction(self.about_action)

        tb = QToolBar("Principal")
        tb.setObjectName("toolbar")
        tb.setMovable(False)
        self.addToolBar(tb)
        tb.addAction(a_open)
        tb.addSeparator()
        # esquema em PDF e modo camadas logo no começo da barra (à direita ficavam esquecidos)
        a = ab["OpenSchematic"]
        a.setText("PDF")
        a.setToolTip("Abrir o esquema elétrico (PDF) desta placa (F4).\n"
                     "Duplo clique num componente leva até ele no esquema.")
        tb.addAction(a)
        a = ab["ToggleLayerMode"]
        a.setText("Camadas")
        a.setToolTip("Modo camadas: mostra as trilhas de cobre de cada camada (F3)\n"
                     "Disponível para arquivos que trazem as trilhas (.tvw e .cad GenCAD)")
        a.setEnabled(False)
        tb.addAction(a)
        tb.addSeparator()
        for aid in ("FlipBoardSide", "RotateClockwise", "FitBoardToScreen", "ToggleDualView"):
            tb.addAction(ab[aid])
        ab["ToggleDualView"].setText("Vista dupla")
        ab["ToggleDualView"].setToolTip("Vista dupla: o outro lado espelhado logo acima, preso à placa (F2)")
        tb.addSeparator()
        # busca: uma caixa para componente (Part) e outra para rede (Net)
        self.part_complete_model = QStringListModel()
        self.net_complete_model = QStringListModel()
        self.completer_model = self.part_complete_model      # nome antigo (compatibilidade)

        def box(label, placeholder, tip, model, slot, width):
            lab = QLabel(f" {label} ")
            lab.setStyleSheet("font-weight: 600;")
            tb.addWidget(lab)
            ed = QLineEdit()
            ed.setPlaceholderText(placeholder)
            ed.setToolTip(tip)
            ed.setClearButtonEnabled(True)
            ed.setMinimumWidth(width)
            ed.setMaximumWidth(int(width * 1.6))
            ed.returnPressed.connect(slot)
            comp = QCompleter(model, self)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            comp.setMaxVisibleItems(18)
            comp.activated.connect(lambda t, ed=ed: (ed.setText(t), slot()))
            ed.setCompleter(comp)
            tb.addWidget(ed)
            return ed
        self.search = box("Part", "componente (Ctrl+F)  ex.: U7000, C12*, 10k, 0402",
                          "Buscar componente: nome, coringa (C12*), valor (10k, 0.47uF) ou encapsulamento (0402)",
                          self.part_complete_model, self.do_search_part, 220)
        self.net_search = box("Net", "rede (Ctrl+Shift+F)  ex.: PP3V3_S5, *VDDCI*",
                              "Buscar rede: nome inteiro, parte do nome ou coringa (*VDD*)",
                              self.net_complete_model, self.do_search_net, 220)

    # -------------------------------------------------------------- preferências / tema / atalhos
    def _set_pref(self, key, value):
        self.prefs[key] = value
        save_prefs(self.settings, self.prefs)
        self._apply_prefs()

    def _apply_prefs(self):
        for d in self.docs:
            self._apply_prefs_to(d.canvas)
        self._update_hud()

    def _apply_prefs_to(self, c):
        p = self.prefs
        o = c.opts
        for k in ("smooth", "smooth_speed", "wheel_step", "show_part_labels", "show_pin_labels",
                  "show_net_names", "show_netweb", "show_boxes", "show_fps", "part_label_scale",
                  "pin_label_scale", "pin_scale", "square_pins", "flip_axis", "hover_info", "netweb_cross",
                  "gnd_subtle", "show_loose", "show_mech"):
            setattr(o, k, p.get(k, True) if k == "show_mech" else p[k])
        if p.get("dual_view"):
            o.flip_axis = "x"        # vista dupla: o lado de baixo abre como um livro (espelho vertical)
        if hasattr(c, "set_continuous"):
            c.set_continuous(p["show_fps"])
        c.theme = self.theme
        c.theme_changed()

    def _apply_keybindings(self):
        mouse = {}
        for aid, a in self.actions_by_id.items():
            seqs = []
            for v in self.kb.get(aid, ["", ""]):
                if not v:
                    continue
                if v in K.MOUSE_NAMES:
                    if v != "Left Mouse Button":
                        mouse[v] = a.trigger
                    continue
                ks = QKeySequence(v)
                if not ks.isEmpty():
                    seqs.append(ks)
            a.setShortcuts(seqs)
        self._mouse_bindings = mouse
        for d in self.docs:
            d.canvas.mouse_bindings = mouse

    def edit_keybindings(self):
        dlg = KeybindingsEditor(self.kb, self)
        if dlg.exec() == QDialog.Accepted:
            self.kb = dlg.kb
            self.settings.setValue("keybindings", K.to_settings(self.kb))
            self._apply_keybindings()

    def edit_prefs(self):
        old = dict(self.prefs)
        dlg = PreferencesDialog(self.prefs, self)
        if dlg.exec() == QDialog.Accepted:
            self.prefs = dlg.prefs
            save_prefs(self.settings, self.prefs)
            self._apply_prefs()
            self.actions_by_id["ToggleFPS"].setChecked(self.prefs["show_fps"])
            self.actions_by_id["ToggleHoverInfo"].setChecked(self.prefs["hover_info"])
            self.actions_by_id["ToggleNetwebCross"].setChecked(self.prefs["netweb_cross"])
            self.actions_by_id["ToggleGndSubtle"].setChecked(self.prefs["gnd_subtle"])
            if any(old[k] != self.prefs[k] for k in ("renderer", "vsync", "msaa", "ui_scale")):
                QMessageBox.information(self, "Preferências",
                                        "Renderizador, VSync, antisserrilhamento e tamanho da interface mudam ao "
                                        "reiniciar o programa.")

    def _load_current_theme(self):
        ini = self.settings.value("theme/ini", "")
        name = self.settings.value("theme/name", "GDTech Escuro")
        if ini:
            try:
                import tempfile
                fd, tmp = tempfile.mkstemp(suffix=".theme")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(ini)
                t = Theme.load(tmp)
                os.unlink(tmp)
                t.name = name
                if name not in BUILTIN:
                    # tema pronto que mudou de nome numa versão nova: reconhece pelas cores e usa o nome atual
                    for bname, bt in BUILTIN.items():
                        if bt.colors == t.colors and bt.colorize == t.colorize:
                            t.name = bname
                            self.settings.setValue("theme/name", bname)
                            break
                return t
            except Exception:
                pass
        return BUILTIN.get(name, BUILTIN["GDTech Escuro"]).copy()

    def set_theme(self, theme):
        self.theme = theme
        self.settings.setValue("theme/name", theme.name)
        self.settings.setValue("theme/ini", theme.to_ini())
        for d in self.docs:
            d.canvas.theme = theme
            d.canvas.theme_changed()

    def _fill_theme_menu(self):
        m = self.theme_menu
        m.clear()
        for name, t in BUILTIN.items():
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(self.theme.name == name)
            a.triggered.connect(lambda _=False, t=t: self.set_theme(t.copy()))
        user = list_user_themes()
        if user:
            m.addSeparator()
            for path in user:
                nm = os.path.splitext(os.path.basename(path))[0]
                a = m.addAction(nm)
                a.setCheckable(True)
                a.setChecked(self.theme.name == nm)
                a.triggered.connect(lambda _=False, p=path: self._theme_from_file(p))
        m.addSeparator()
        m.addAction("Importar tema (.theme)...", self._import_theme)
        m.addAction("Editor de tema...", self.edit_theme)

    def _theme_from_file(self, path):
        try:
            self.set_theme(Theme.load(path))
        except Exception as e:
            QMessageBox.warning(self, "Tema", f"Não foi possível ler o tema:\n{e}")

    def _import_theme(self):
        path, _ = QFileDialog.getOpenFileName(self, "Importar tema", "", "Tema (*.theme);;Todos (*)")
        if path:
            try:
                t = Theme.load(path)
            except Exception as e:
                QMessageBox.warning(self, "Tema", f"Não foi possível ler o tema:\n{e}")
                return
            import shutil
            dst = os.path.join(user_dir("themes"), os.path.basename(path))
            if os.path.abspath(dst) != os.path.abspath(path):
                shutil.copyfile(path, dst)
            self.set_theme(t)

    def edit_theme(self):
        if self.theme_editor is not None and self.theme_editor.isVisible():
            self.theme_editor.raise_()
            return
        self.theme_editor = ThemeEditor(self.theme, self)
        self.theme_editor.themeChanged.connect(self.set_theme)
        self.theme_editor.show()

    # -------------------------------------------------------------- HUD
    def _on_fps(self, fps):
        self._fps = fps
        if self.prefs["show_fps"]:
            self._update_hud()

    def _update_hud(self):
        c = self.canvas
        if c is None or not hasattr(c, "_hud"):
            return
        parts = ["TOP" if c.side == TOP else "BOTTOM"]
        if getattr(c, "layer_mode", False) and c.has_layers():
            vis = [c.board.layers[i].name for i in range(len(c.board.layers)) if c.layer_visible(i)]
            parts.append("camadas: " + (", ".join(vis[:4]) + ("…" if len(vis) > 4 else "") if vis else "nenhuma"))
        if getattr(c, "dual_view", False):
            where = "ao lado" if c.board is not None and c.mirror_beside() else "acima"
            parts[0] += " + " + ("BOTTOM" if c.side == TOP else "TOP") + f" espelhado ({where})"
        if c.rotation:
            parts.append(f"{c.rotation * 90}°")
        if c.mirror_h or c.mirror_v:
            parts.append("espelhado")
        if self.prefs.get("show_fps"):
            parts.append(f"{self._fps:.0f} FPS · {c.renderer_name}")
        c._hud.setText("  ·  ".join(parts))
        c._hud.setStyleSheet("QLabel { background: rgba(20,24,31,200); border-radius: 6px; padding: 4px 10px;"
                               f" font-weight: 600; color: {'#e3b341' if c.side == TOP else '#8fd3ff'}; }}")
        c._hud.adjustSize()

    def _build_docks(self):
        # lista de componentes / redes
        left = QWidget()
        lv = QVBoxLayout(left); lv.setContentsMargins(4, 4, 4, 4)
        tabs = QTabWidget()
        self.part_model = QStringListModel()
        self.net_model = QStringListModel()
        self.part_list, self.part_filter = self._filtered_list(self.part_model, "Filtrar componentes…")
        self.net_list, self.net_filter = self._filtered_list(self.net_model, "Filtrar redes…")
        self.part_list.activated.connect(lambda idx: self._goto_part(idx.data()))
        self.part_list.clicked.connect(lambda idx: self._goto_part(idx.data()))
        self.net_list.activated.connect(lambda idx: self._goto_net(idx.data()))
        self.net_list.clicked.connect(lambda idx: self._goto_net(idx.data()))
        tabs.addTab(self._wrap(self.part_filter, self.part_list), "Componentes")
        tabs.addTab(self._wrap(self.net_filter, self.net_list), "Redes")
        lv.addWidget(tabs)
        d = QDockWidget("Lista", self)
        d.setObjectName("dock_list")
        d.setWidget(left)
        d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.LeftDockWidgetArea, d)

        # painel de informações
        right = QWidget()
        rv = QVBoxLayout(right); rv.setContentsMargins(6, 6, 6, 6)
        self.info_title = QLabel("Nada selecionado")
        self.info_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.info_title.setWordWrap(True)
        self.info_sub = QLabel("Clique em um pino ou componente.\nDuplo clique: mostra no esquema.")
        self.info_sub.setWordWrap(True)
        self.info_sub.setStyleSheet("color: #9aa3b2;")
        self.info_table = QTableWidget(0, 3)
        self.info_table.verticalHeader().setVisible(False)
        self.info_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.info_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.info_table.setSortingEnabled(True)
        self.info_table.cellDoubleClicked.connect(self._table_activate)
        self.info_table.cellClicked.connect(self._table_click)
        rv.addWidget(self.info_title)
        rv.addWidget(self.info_sub)
        rv.addWidget(self.info_table, 1)
        d2 = QDockWidget("Informações", self)
        d2.setObjectName("dock_info")
        d2.setWidget(right)
        d2.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        self.addDockWidget(Qt.RightDockWidgetArea, d2)
        self.resizeDocks([d, d2], [230, 330], Qt.Horizontal)

        # camadas (modo camadas)
        lw = QWidget()
        ll = QVBoxLayout(lw); ll.setContentsMargins(6, 6, 6, 6)
        self.layer_list = QListWidget()
        self.layer_list.itemChanged.connect(self._layer_item_changed)
        self.layer_list.itemDoubleClicked.connect(
            lambda it: (self.canvas.solo_layer(self.layer_list.row(it)), self._sync_layer_panel()))
        row = QHBoxLayout()
        b_all = QPushButton("Todas")
        b_none = QPushButton("Nenhuma")
        b_all.clicked.connect(lambda: self._set_all_layers(True))
        b_none.clicked.connect(lambda: self._set_all_layers(False))
        row.addWidget(b_all)
        row.addWidget(b_none)
        hint = QLabel("Duplo clique: só esta camada\nPgUp / PgDn: uma camada por vez\nClique numa trilha: seleciona a rede")
        hint.setStyleSheet("color: #7d8696; font-size: 11px;")
        ll.addWidget(self.layer_list, 1)
        ll.addLayout(row)
        ll.addWidget(hint)
        self.layer_dock = QDockWidget("Camadas", self)
        self.layer_dock.setObjectName("dock_layers")
        self.layer_dock.setWidget(lw)
        self.layer_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable |
                                    QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, self.layer_dock)
        self.layer_dock.hide()

        # ferramentas: busca, caçar curto, notas, comparar
        from .toolpanel import ToolPanel
        self.tools = ToolPanel(self)
        self.tools_dock = QDockWidget("Ferramentas", self)
        self.tools_dock.setObjectName("dock_tools")
        self.tools_dock.setWidget(self.tools)
        self.tools_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable |
                                    QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, self.tools_dock)
        self._layer_list_board = None

    # -------------------------------------------------------------- modo camadas
    def toggle_layer_mode(self, on):
        c = self.canvas
        if on and not c.has_layers():
            a = self.actions_by_id["ToggleLayerMode"]
            a.blockSignals(True)
            a.setChecked(False)
            a.blockSignals(False)
            self.statusBar().showMessage("Este arquivo não traz as trilhas de cobre (só .tvw e .cad GenCAD têm).",
                                         6000)
            return
        c.set_layer_mode(on)
        self._sync_layer_panel()

    def _set_all_layers(self, on):
        c = self.canvas
        if not c.has_layers():
            return
        for i in range(len(c.board.layers)):
            c._layer_vis[i] = on
        c.layers_updated()
        c.request_frame()
        self._sync_layer_panel()

    def _layer_item_changed(self, it):
        if getattr(self, "_syncing_layers", False):
            return
        self.canvas.set_layer_visible(self.layer_list.row(it), it.checkState() == Qt.Checked)
        self._update_hud()

    def _sync_layer_panel(self):
        """Atualiza botão e lista de camadas de acordo com a aba atual."""
        if not self._ui_ready or self.canvas is None:
            return
        c = self.canvas
        has = c.has_layers()
        a = self.actions_by_id["ToggleLayerMode"]
        a.setEnabled(has)
        a.blockSignals(True)
        a.setChecked(bool(c.layer_mode))
        a.blockSignals(False)
        self._syncing_layers = True
        try:
            if self._layer_list_board is not c.board:
                self.layer_list.clear()
                self._layer_list_board = c.board
                if has:
                    for i, L in enumerate(c.board.layers):
                        it = QListWidgetItem(L.name)
                        pm = QPixmap(14, 14)
                        pm.fill(QColor(*c.layer_color(i)))
                        it.setIcon(QIcon(pm))
                        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                        info = []
                        if len(L.seg):
                            info.append(f"{len(L.seg)} trechos de trilha")
                        if L.polys:
                            info.append(f"{len(L.polys)} áreas de cobre")
                        it.setToolTip(" · ".join(info))
                        self.layer_list.addItem(it)
            for i in range(self.layer_list.count()):
                self.layer_list.item(i).setCheckState(Qt.Checked if c.layer_visible(i) else Qt.Unchecked)
        finally:
            self._syncing_layers = False
        self.layer_dock.setVisible(bool(c.layer_mode and has))
        self._update_hud()

    # -------------------------------------------------------------- menu do botão direito / notas
    def _context_menu(self, pi, pin, pos):
        b = self.board
        if b is None:
            return
        from .notes import TAGS, hexcolor
        from . import tools as T
        m = QMenu(self)
        if pi >= 0:
            part = b.parts[pi]
            store = self.current_doc().notes
            cur = store.get(part.name) if store else None
            title = m.addAction(part.name + (f"  ·  {T.value_of(part, self.canvas.ptype[pi])}"
                                              if T.value_of(part, self.canvas.ptype[pi]) else ""))
            title.setEnabled(False)
            sub = m.addMenu("Marcar como")
            for tag, col in TAGS[:-1]:
                pm = QPixmap(12, 12)
                pm.fill(QColor(hexcolor(col)))
                a = sub.addAction(QIcon(pm), tag)
                a.setCheckable(True)
                a.setChecked(bool(cur) and cur.get("tag") == tag)
                a.triggered.connect(lambda _c=False, tag=tag, n=part.name:
                                    self.set_note(n, tag, (store.get(n) or {}).get("text", "")))
            m.addAction("Escrever nota…", lambda n=part.name: self.edit_note(n))
            if cur:
                m.addAction("Remover nota", lambda n=part.name: self.remove_note(n))
            m.addSeparator()
            q = T.datasheet_query(part, self.canvas.ptype[pi])
            m.addAction(f"Procurar datasheet: {q}", lambda q=q: self._web_search(q, "google"))
            m.addAction("Procurar no alldatasheet", lambda q=q: self._web_search(q, "alldatasheet"))
            m.addSeparator()
            m.addAction("Copiar nome", lambda: QApplication.clipboard().setText(part.name))
            if part.mfg:
                m.addAction("Copiar código / descrição", lambda: QApplication.clipboard().setText(part.mfg))
            if self.pdf_win is not None and self.pdf_win.isVisible():
                m.addAction("Procurar no esquema (PDF)", lambda n=part.name: self.pdf_win.find(n, "part"))
            m.addSeparator()
            a = m.addAction("Procurar esta peça em outras placas (sucata)…", lambda pi=pi: self.donor_search_part(pi))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
        if pin >= 0:
            ni = int(b.net[pin])
            m.addSeparator()
            t = m.addAction(f"Rede {b.nets[ni]}")
            t.setEnabled(False)
            m.addAction("Caçar curto nesta rede", lambda: self.tools.use_selected_net(ni))
            m.addAction("Seguir esta rede", lambda: self.tools.follow_selected_net(ni))
            m.addAction("Copiar nome da rede", lambda: QApplication.clipboard().setText(b.nets[ni]))
            if self.pdf_win is not None and self.pdf_win.isVisible():
                m.addAction("Procurar a rede no esquema (PDF)", lambda: self.pdf_win.find(b.nets[ni], "net"))
        if not m.isEmpty():
            m.exec(pos)

    def _web_search(self, q, where):
        from urllib.parse import quote_plus
        if where == "alldatasheet":
            url = "https://www.alldatasheet.com/view.jsp?Searchword=" + quote_plus(q)
        else:
            url = "https://www.google.com/search?q=" + quote_plus(q + " datasheet")
        QDesktopServices.openUrl(QUrl(url))

    def set_note(self, name, tag, text=""):
        d = self.current_doc()
        if d is None or d.notes is None:
            return
        d.notes.set(name, tag, text)
        self._notes_changed(d)

    def edit_note(self, name):
        d = self.current_doc()
        if d is None or d.notes is None:
            return
        from .notes import TAGS
        cur = d.notes.get(name) or {}
        tags = [t for t, _ in TAGS]
        tag, ok = QInputDialog.getItem(self, f"Nota — {name}", "Marcação:", tags,
                                       tags.index(cur.get("tag", "Nota")) if cur.get("tag") in tags else len(tags) - 1,
                                       False)
        if not ok:
            return
        text, ok = QInputDialog.getText(self, f"Nota — {name}", "Texto (opcional):", text=cur.get("text", ""))
        if not ok:
            return
        d.notes.set(name, tag, text.strip())
        self._notes_changed(d)

    def remove_note(self, name):
        d = self.current_doc()
        if d is None or d.notes is None:
            return
        d.notes.remove(name)
        self._notes_changed(d)

    def _notes_changed(self, d):
        d.canvas.set_note_marks(d.notes.marks(d.board))
        d.canvas.hover_part = None          # atualiza a caixa de informações na próxima passada do mouse
        if d.canvas._card is not None:
            d.canvas._card.hide_card()
        self.tools._refresh_notes()

    # -------------------------------------------------------------- esquema elétrico (PDF)
    def _schematic_window(self):
        if self.pdf_win is None:
            try:
                from .pdfview import SchematicWindow
            except ImportError as e:
                QMessageBox.warning(self, APP_NAME, "O visualizador de PDF precisa do pacote pypdfium2 "
                                    "(ou do módulo QtPdf do PySide6).\n"
                                    "Rode: python -m pip install --upgrade pypdfium2 PySide6\n\n" + str(e))
                return None
            self.pdf_win = SchematicWindow(self)
            self.pdf_win.pdfChosen.connect(self._pdf_chosen)
            if hasattr(self.pdf_win, "lookupRequested"):
                self.pdf_win.lookupRequested.connect(self._lookup_from_pdf)
            if hasattr(self.pdf_win, "donorRequested"):
                self.pdf_win.donorRequested.connect(self.donor_search_text)
        return self.pdf_win

    # -------------------------------------------------------------- peça em outras placas (sucata)
    def _donor_idx(self):
        if self.donor_index is None:
            from .donorpanel import DonorIndex
            self.donor_index = DonorIndex(os.path.join(user_dir(""), "pecas.sqlite"), self)
        return self.donor_index

    def donor_reindex(self):
        """Atualiza o índice (só lê arquivos novos ou alterados) em segundo plano."""
        roots = self.home.roots()
        if roots:
            self._donor_idx().start(roots, self.keys())

    def _donor_auto(self):
        if self.prefs.get("donor_auto_index", True):
            self.donor_reindex()

    def _donor_window(self):
        if self.donor_win is None:
            from .donorpanel import DonorWindow
            w = DonorWindow(self._donor_idx(), self.home.roots, self)
            w.openBoard.connect(self.open_board_at_part)
            w.openPdf.connect(self.open_pdf_hit)
            w.wantFolder.connect(self.home.add_folder)
            w.reindexRequested.connect(self.donor_reindex)
            self.donor_win = w
        return self.donor_win

    def donor_search_part(self, pi):
        from . import donor
        d = self.current_doc()
        b = self.board
        if b is None or d is None:
            return
        p = b.parts[pi]
        q = donor.query_for_part(p.name, p.mfg, getattr(p, "footprint", ""), d.path)
        self._donor_window().ask(q, d.path)
        if not self._donor_idx().running():
            self.donor_reindex()

    def donor_search_text(self, text):
        """Texto escolhido no esquema: se for um componente desta placa usa a descrição dele; senão, o próprio
        texto como código."""
        from . import donor
        t = (text or "").strip().strip(",;:()[]{}")
        b = self.board
        d = self.current_doc()
        if b is not None and t.upper() in b.part_index:
            self.donor_search_part(b.part_index[t.upper()])
            return
        q = donor.Query("codigo", code=t, label=f"“{t}” (do esquema)")
        self._donor_window().ask(q, d.path if d else "")

    def donor_search_selected(self):
        c = self.canvas
        if self.board is not None and c is not None and c.sel_part is not None:
            self.donor_search_part(c.sel_part)
        else:
            w = self._donor_window()
            w.show()
            w.raise_()
            w.activateWindow()

    def _select_part_name(self, d, name):
        pi = d.board.part_index.get((name or "").upper()) if d.board is not None else None
        if pi is not None:
            d.canvas.select_part(pi, zoom=True)

    def open_board_at_part(self, path, name):
        ap = os.path.abspath(path)
        for k, d in enumerate(self.docs):
            if d.path and os.path.abspath(d.path) == ap:
                self.tabbar.setCurrentIndex(k)
                self._select_part_name(d, name)
                self.raise_()
                self.activateWindow()
                return
        self._pending_select[ap] = name
        self.open_file(path)
        self.raise_()
        self.activateWindow()

    def open_pdf_hit(self, path, term, page):
        w = self._schematic_window()
        if w is None:
            return
        if hasattr(w, "open_hit"):
            w.open_hit(path, term, page)
        else:
            w.load(path)
            w.find(term, "text")
            w.show()

    def _lookup_from_pdf(self, text):
        """Texto escolhido no esquema (botão direito → Mostrar na placa): acha na placa da aba atual."""
        t = (text or "").strip().strip(",;:()[]{}")
        if not t or self.board is None:
            return
        b = self.board
        if t.upper() in b.part_index:
            self.search.setText(t)
            self.do_search_part()
        elif t in b.net_index or any(n.upper() == t.upper() for n in b.net_index):
            self.net_search.setText(t)
            self.do_search_net()
        else:
            self.do_search(t)
        self.raise_()
        self.activateWindow()
        self.canvas.setFocus()

    def open_schematic(self):
        d = self.current_doc()
        w = self._schematic_window()
        if w is None or d is None:
            return
        if not d.pdf_path or not os.path.exists(d.pdf_path):
            start = os.path.dirname(d.path) if d.path else self.settings.value("last_dir", "")
            path, _ = QFileDialog.getOpenFileName(self, "Esquema elétrico (PDF) desta placa", start,
                                                  "PDF (*.pdf);;Todos os arquivos (*)")
            if not path:
                return
            self._pdf_chosen(path)
        w.show_doc_pdf(d.pdf_path)
        w.show()
        w.raise_()
        w.activateWindow()

    def _pdf_chosen(self, path):
        """O usuário escolheu (ou trocou) o PDF da aba atual: lembra para a próxima vez."""
        d = self.current_doc()
        if d is None:
            return
        d.pdf_path = path
        if d.path:
            self.settings.setValue("schematic/" + _settings_key(d.path), path)

    def _item_double_clicked(self, kind, name):
        w = self.pdf_win
        if w is None or not w.isVisible() or not name:
            return
        if kind == "pin":
            part, num, net, npins = (name.split("\t") + ["", "", "", ""])[:4]
            if hasattr(w, "find_pin"):
                w.find_pin(part, num, net, int(npins) if npins.isdigit() else 99)
            else:
                w.find(part, "part")
            return
        w.find(name, kind)

    def _filtered_list(self, model, placeholder):
        proxy = QSortFilterProxyModel(self)
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        lst = QListView()
        lst.setModel(proxy)
        lst.setUniformItemSizes(True)
        lst.setEditTriggers(QAbstractItemView.NoEditTriggers)
        f = QLineEdit()
        f.setPlaceholderText(placeholder)
        f.setClearButtonEnabled(True)
        f.textChanged.connect(proxy.setFilterFixedString)
        return lst, f

    @staticmethod
    def _wrap(*widgets):
        w = QWidget()
        l = QVBoxLayout(w); l.setContentsMargins(0, 4, 0, 0)
        for x in widgets:
            l.addWidget(x)
        return w

    def _build_status(self):
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.st_file = QLabel("")
        self.st_pos = QLabel("")
        self.st_credit = QLabel(f"criado por {ORG}")
        self.st_credit.setStyleSheet("color: #6c7686;")
        sb.addWidget(self.st_file, 1)
        sb.addPermanentWidget(self.st_pos)
        sb.addPermanentWidget(self.st_credit)

    # -------------------------------------------------------------- arquivo
    def open_paths(self, paths):
        """Abre vários arquivos. PDFs soltos junto com boardviews ficam associados a eles."""
        from .library import pair_pdfs
        paths = [p for p in paths if p]
        pdfs = [p for p in paths if p.lower().endswith(".pdf")]
        boards = [p for p in paths if not p.lower().endswith(".pdf")]
        if boards and pdfs:
            self._pending_pdf.update(pair_pdfs(boards, pdfs))
        elif pdfs:
            self.open_file(pdfs[0])           # só PDF: vai para a aba atual
            return
        for p in boards:
            self.open_file(p)

    def _open_with_pdf(self, path, pdf=""):
        if pdf:
            self._pending_pdf[os.path.abspath(path)] = pdf
        self.open_file(path)

    # -------------------------------------------------------------- sessão (abas da última vez)
    def _camera_state(self, c):
        return {"side": int(c.side), "rotation": int(c.rotation), "mh": bool(c.mirror_h), "mv": bool(c.mirror_v),
                "cx": float(c.cx), "cy": float(c.cy), "scale": float(c.scale)}

    def save_session(self):
        import json
        tabs = [{"path": os.path.abspath(d.path), "cam": self._camera_state(d.canvas)}
                for d in self.docs if d.board is not None and d.path]
        cur = self.current_doc()
        self.settings.setValue("session", json.dumps({
            "tabs": tabs, "current": os.path.abspath(cur.path) if cur and cur.path else ""}))

    def restore_session(self):
        import json
        try:
            s = json.loads(self.settings.value("session", "") or "{}")
        except ValueError:
            return
        tabs = [t for t in s.get("tabs", []) if os.path.isfile(t.get("path", ""))]
        if not tabs:
            return
        for t in tabs:
            self._pending_state[os.path.abspath(t["path"])] = t.get("cam") or {}
        # as abas voltam, mas o programa fica na tela de Início (uma aba nova no fim)
        self._pending_current = HOME_AFTER_RESTORE
        self.open_paths([t["path"] for t in tabs])

    def _apply_camera(self, c, st):
        try:
            c.side = int(st.get("side", c.side))
            c.rotation = int(st.get("rotation", 0)) % 4
            c.mirror_h, c.mirror_v = bool(st.get("mh")), bool(st.get("mv"))
            c.scale = c.t_scale = float(st["scale"])
            c.cx = c.t_cx = float(st["cx"])
            c.cy = c.t_cy = float(st["cy"])
            c._anchor = None
            c.request_frame()
            c.viewChanged.emit()
        except (KeyError, TypeError, ValueError):
            pass

    def keys(self):
        return {
            "fz": parse_fz_key(self.settings.value("keys/fz", "") or ""),
            "cae": parse_fz_key(self.settings.value("keys/cae", "") or ""),
            "xzz": parse_xzz_key(self.settings.value("keys/xzz", "") or ""),
        }

    def open_dialog(self):
        exts = " ".join("*" + e for e in parsers.SUPPORTED_EXTS)
        last = self.settings.value("last_dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "Abrir boardview", last,
                                                f"Boardview ({exts});;Todos os arquivos (*)")
        self.open_paths(paths)

    def open_file(self, path):
        if path.lower().endswith(".pdf"):
            self._pdf_chosen(path)
            self.open_schematic()
            return
        ap = os.path.abspath(path)
        for i, d in enumerate(self.docs):          # já aberto: só vai para a aba
            if d.path and os.path.abspath(d.path) == ap:
                self.tabbar.setCurrentIndex(i)
                return
        if self._thread is not None:
            if ap not in self._queue:
                self._queue.append(ap)
            return
        self.settings.setValue("last_dir", os.path.dirname(path))
        self.st_file.setText(f"Abrindo {os.path.basename(path)}…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        self._thread = QThread()
        self._loader = Loader(path, self.keys())
        self._loader.moveToThread(self._thread)
        self._thread.started.connect(self._loader.run)
        self._loader.done.connect(self._loaded)
        self._loader.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._thread_done)
        self._thread.start()

    def _thread_done(self):
        self._thread.deleteLater()
        self._thread = None
        if self._queue:
            self.open_file(self._queue.pop(0))

    def _loaded(self, board, err):
        QApplication.restoreOverrideCursor()
        path = self._loader.path
        if board is None:
            self.st_file.setText("")
            box = QMessageBox(QMessageBox.Warning, APP_NAME, f"Não foi possível abrir:\n{os.path.basename(path)}",
                              parent=self)
            box.setInformativeText(err.split("\n\n")[0])
            if "\n\n" in err:
                box.setDetailedText(err)
            if "chave" in err.lower():
                b = box.addButton("Configurar chaves…", QMessageBox.ActionRole)
                box.addButton(QMessageBox.Ok)
                box.exec()
                if box.clickedButton() == b:
                    if self.edit_keys():
                        self.open_file(path)
                return
            box.exec()
            return
        d = self.current_doc()
        if d is None or d.board is not None or d.is_home:
            d = self._new_doc()
        d.board = board
        d.path = path
        ap = os.path.abspath(path)
        d.pdf_path = self.settings.value("schematic/" + _settings_key(path), "") or ""
        auto_pdf = ""
        if ap in self._pending_pdf:                       # arrastado junto / biblioteca
            d.pdf_path = self._pending_pdf.pop(ap)
            self.settings.setValue("schematic/" + _settings_key(path), d.pdf_path)
            auto_pdf = d.pdf_path
        elif not d.pdf_path or not os.path.exists(d.pdf_path):
            from .library import find_pdf_for
            found = find_pdf_for(path)
            if found:
                d.pdf_path = found
                self.settings.setValue("schematic/" + _settings_key(path), found)
                auto_pdf = found
        d.parts_list = sorted({p.name for p in board.parts}, key=natural_key)
        d.nets_list = sorted([n for n in board.nets if n != "UNCONNECTED"], key=natural_key)
        d.canvas.set_board(board)
        from .notes import NoteStore
        d.notes = NoteStore(user_dir(""), path)
        d.canvas.note_lookup = lambda pi, d=d: d.notes.lookup(d.board.parts[pi].name) if d.board else None
        d.canvas.set_note_marks(d.notes.marks(board))
        i = self.docs.index(d)
        self.tabbar.setTabText(i, os.path.basename(path))
        self.tabbar.setTabToolTip(i, path)
        self._add_recent(path)
        if ap in self._pending_state:
            self._apply_camera(d.canvas, self._pending_state.pop(ap))
        if self.tabbar.currentIndex() == i:
            self._tab_changed(i)
        else:
            self.tabbar.setCurrentIndex(i)
        if ap in self._pending_select:                    # aberto pela busca de peça em outras placas
            self._select_part_name(d, self._pending_select.pop(ap))
        self.update_status()
        if auto_pdf:
            self.statusBar().showMessage(f"Esquema desta placa: {os.path.basename(auto_pdf)}  (F4 abre)", 8000)
        # miniatura para a tela de início
        try:
            from .library import make_thumbnail, thumb_path
            make_thumbnail(board, self.theme, thumb_path(user_dir(""), path))
        except Exception:  # noqa
            traceback.print_exc()
        # abas da última vez: no fim, volta para a que estava aberta
        if self._pending_current and not self._queue and not self._pending_state:
            if self._pending_current == HOME_AFTER_RESTORE:
                self._pending_current = None
                self.go_home()
                return
            for k, dd in enumerate(self.docs):
                if dd.path and os.path.abspath(dd.path) == os.path.abspath(self._pending_current):
                    self.tabbar.setCurrentIndex(k)
            self._pending_current = None

    def _add_recent(self, path):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        path = os.path.abspath(path)
        rec = [path] + [r for r in rec if r != path]
        self.settings.setValue("recent", rec[:12])
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.clear()
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            a = QAction(os.path.basename(r), self)
            a.setToolTip(r)
            a.triggered.connect(lambda checked=False, p=r: self.open_file(p))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(rec))

    def edit_keys(self):
        return KeysDialog(self.settings, self).exec() == QDialog.Accepted

    # -------------------------------------------------------------- busca / navegação
    def do_search_part(self):
        """Caixa Part: só componentes (nome, coringa, valor, encapsulamento)."""
        if self.board is None:
            return
        t = self.search.text().strip()
        if not t:
            return
        self.tools.clear_search()
        b = self.board
        pi = b.part_index.get(t.upper())
        if pi is not None:
            self.canvas.select_part(pi, zoom=True)
            return
        if self.tools.run_search(t, kind="part"):
            return
        up = t.upper()
        parts = [p.index for p in b.parts if up in p.name.upper()]
        if len(parts) == 1:
            self.canvas.select_part(parts[0], zoom=True)
        elif parts:
            self.tools.run_search("*" + t + "*", kind="part")
        else:
            self.statusBar().showMessage(f"Nenhum componente “{t}”", 4000)

    def do_search_net(self):
        """Caixa Net: só redes (nome exato, sem diferenciar maiúsculas, parte do nome ou coringa)."""
        if self.board is None:
            return
        t = self.net_search.text().strip()
        if not t:
            return
        self.tools.clear_search()
        b = self.board
        if t in b.net_index:
            self.canvas.goto_net(b.net_index[t])
            return
        up = t.upper()
        for n, i in b.net_index.items():
            if n.upper() == up:
                self.canvas.goto_net(i)
                return
        if any(ch in t for ch in "*?["):
            if self.tools.run_search(t, kind="net"):
                return
        nets = [i for n, i in b.net_index.items() if up in n.upper() and n != "UNCONNECTED"]
        if len(nets) == 1:
            self.canvas.goto_net(nets[0])
        elif nets:
            self.tools.run_search("*" + t + "*", kind="net")
        else:
            self.statusBar().showMessage(f"Nenhuma rede “{t}”", 4000)

    def do_search(self, t=None):
        """Busca geral (componente ou rede), usada por "Mostrar na placa" do esquema."""
        if self.board is None:
            return
        t = (self.search.text() if t is None else t).strip()
        if not t:
            return
        self.tools.clear_search()
        b = self.board
        pi = b.part_index.get(t.upper())
        if pi is not None:
            self.canvas.select_part(pi, zoom=True)
            return
        if t in b.net_index:
            self.canvas.goto_net(b.net_index[t])
            return
        up = t.upper()
        for n, i in b.net_index.items():
            if n.upper() == up:
                self.canvas.goto_net(i)
                return
        # busca especial: coringa, valor, encapsulamento
        if self.tools.run_search(t):
            return
        # parte do nome: um resultado vai direto; vários aparecem todos acesos na placa
        parts = [p.index for p in b.parts if up in p.name.upper()]
        nets = [i for n, i in b.net_index.items() if up in n.upper() and n != "UNCONNECTED"]
        if len(parts) + len(nets) == 1:
            if parts:
                self.canvas.select_part(parts[0], zoom=True)
            else:
                self.canvas.goto_net(nets[0])
            return
        if parts or nets:
            self.tools.run_search("*" + t + "*")
            return
        self.statusBar().showMessage(f"Nada encontrado para “{t}”", 4000)

    def _goto_part(self, name):
        if self.board and name:
            pi = self.board.part_index.get(name.upper())
            if pi is not None:
                self.canvas.select_part(pi, zoom=True)

    def _goto_net(self, name):
        if self.board and name in self.board.net_index:
            # clicked + activated chegam juntos no mesmo clique: não pular para o 2º pino por isso
            import time
            key = (id(self.canvas), name)
            now = time.monotonic()
            if getattr(self, "_last_goto_net", (None, 0.0))[0] == key and now - self._last_goto_net[1] < 0.4:
                return
            self._last_goto_net = (key, now)
            self.canvas.goto_net(self.board.net_index[name])

    def _table_click(self, row, col):
        it = self.info_table.item(row, 0)
        if it is None:
            return
        pin = it.data(Qt.UserRole)
        if pin is not None and self.board is not None:
            # destaca o pino sem mudar a tabela inteira
            self.canvas.highlight_pin(int(pin))

    def _table_activate(self, row, col):
        it = self.info_table.item(row, 0)
        if it is None or self.board is None:
            return
        pin = it.data(Qt.UserRole)
        if pin is None:
            return
        pin = int(pin)
        if self._info_mode == "part" and col == 2:
            self.canvas.select_net(int(self.board.net[pin]), zoom=True, keep_pin=pin)
        else:
            self.canvas.select_pin(pin, zoom=True)

    # -------------------------------------------------------------- painel de informações
    def on_selection(self):
        b = self.board
        c = self.canvas
        t = self.info_table
        t.setSortingEnabled(False)
        t.setRowCount(0)
        self._info_mode = None
        if b is None:
            return
        side_txt = {TOP: "Superior", BOTTOM: "Inferior", BOTH: "Ambos"}
        if c.sel_pin is not None:
            i = c.sel_pin
            net = b.nets[b.net[i]]
            pi = int(b.part[i])
            part = b.parts[pi] if pi >= 0 else None
            self.info_title.setText(f"Pino {b.pin_label(i)}")
            lines = [f"Rede: <b>{net}</b>"]
            if b.pname[i] != b.num[i]:
                lines.append(f"Nome do pino: {b.pname[i]}")
            if b.probe[i] > 0 and b.probe[i] != 1:
                lines.append(f"Probe: {b.probe[i]}")
            lines.append(f"Lado: {side_txt.get(int(b.side[i]), '?')} · X {b.x[i]:.0f}  Y {b.y[i]:.0f}")
            if part and part.mfg:
                lines.append(f"Componente: {part.mfg}")
            self.info_sub.setText("<br>".join(lines))
            self._fill_net_table(b.net[i])
        elif c.sel_part is not None:
            part = b.parts[c.sel_part]
            self.info_title.setText(part.name)
            lines = [f"{len(part.pins)} pinos · {'SMD' if part.smd else 'PTH'} · lado {side_txt.get(part.side, '?')}"]
            if part.mfg:
                lines.append(html.escape(part.mfg))
            from . import info as _info
            extra = []
            val = _info.value_of(part, c.ptype[c.sel_part])
            if val:
                extra.append(f"Valor: <b>{html.escape(val)}</b>")
            sz = _info.size_text(b, part)
            if sz:
                extra.append(f"Tamanho: {html.escape(sz)}")
            if part.footprint:
                extra.append(f"Footprint: {html.escape(part.footprint)}")
            if extra:
                lines.append(" · ".join(extra))
            self.info_sub.setText("<br>".join(lines))
            self._info_mode = "part"
            t.setColumnCount(3)
            t.setHorizontalHeaderLabels(["Pino", "Nome", "Rede"])
            pins = sorted(part.pins, key=lambda i: natural_key(b.num[i]))
            t.setRowCount(len(pins))
            for r, i in enumerate(pins):
                a = QTableWidgetItem(b.num[i]); a.setData(Qt.UserRole, int(i))
                t.setItem(r, 0, a)
                t.setItem(r, 1, QTableWidgetItem(b.pname[i]))
                it = QTableWidgetItem(b.nets[b.net[i]])
                if b.net_is_gnd[b.net[i]]:
                    it.setForeground(QColor("#7d8696"))
                t.setItem(r, 2, it)
            self._fit_table()
        elif c.sel_net is not None:
            ni = c.sel_net
            self.info_title.setText(b.nets[ni])
            self.info_sub.setText(f"{len(b.net_pins[ni])} conexões")
            self._fill_net_table(ni)
        else:
            self.info_title.setText("Nada selecionado")
            self.info_sub.setText("Clique em um pino ou componente.<br>Duplo clique: mostra no esquema.")
        t.setSortingEnabled(True)
        self.update_status()

    def _fill_net_table(self, ni):
        b = self.board
        t = self.info_table
        self._info_mode = "net"
        t.setColumnCount(3)
        t.setHorizontalHeaderLabels(["Componente", "Pino", "Lado"])
        pins = b.net_pins[ni]
        if len(pins) > 5000:
            pins = pins[:5000]
        rows = sorted(pins, key=lambda i: (natural_key(b.parts[b.part[i]].name) if b.part[i] >= 0 else ["~"],
                                           natural_key(b.num[i])))
        t.setRowCount(len(rows))
        sd = {TOP: "T", BOTTOM: "B", BOTH: "T/B"}
        for r, i in enumerate(rows):
            pi = int(b.part[i])
            a = QTableWidgetItem(b.parts[pi].name if pi >= 0 else "(test pad)")
            a.setData(Qt.UserRole, int(i))
            t.setItem(r, 0, a)
            t.setItem(r, 1, QTableWidgetItem(b.num[i] if b.pname[i] == b.num[i] else f"{b.num[i]} ({b.pname[i]})"))
            t.setItem(r, 2, QTableWidgetItem(sd.get(int(b.side[i]), "?")))
        self._fit_table()

    def _fit_table(self):
        h = self.info_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)

    # -------------------------------------------------------------- status
    def on_cursor(self, x, y):
        self.st_pos.setText(f"X {x:,.0f}  Y {y:,.0f}".replace(",", "."))

    def update_status(self):
        b = self.board
        if b is None:
            return
        self.st_file.setText(f"{os.path.basename(b.path)}  ·  formato {b.format_name}  ·  {b.stats()}")

    # -------------------------------------------------------------- outros
    def show_tips(self):
        from .tips import TipsDialog
        dlg = TipsDialog(self, show_on_start=self.prefs.get("tips_on_start", False))
        dlg.exec()
        self.prefs["tips_on_start"] = dlg.again.isChecked()
        save_prefs(self.settings, self.prefs)

    def show_shortcuts(self):
        lines = ["Roda do mouse: zoom no cursor", "Arrastar: mover a placa",
                 "Clique: seleciona pino / componente", "Duplo clique: mostra no esquema (sem mexer no zoom)", ""]
        for aid, label, _, _ in K.ACTIONS:
            keys = [k for k in self.kb.get(aid, ["", ""]) if k]
            if keys:
                lines.append(f"{label}: {'  ou  '.join(keys)}")
        QMessageBox.information(self, "Atalhos", "\n".join(lines))

    def show_about(self):
        w = getattr(self, "_about_win", None)
        if w is not None and w.isVisible():       # já aberta: só traz para a frente
            w.raise_()
            w.activateWindow()
            return
        from .about import AboutDialog
        try:
            from .icon_data import app_icon
            icon = app_icon()
        except Exception:  # noqa
            icon = None
        w = AboutDialog(self, icon)
        w.setAttribute(Qt.WA_DeleteOnClose)
        w.destroyed.connect(lambda *_: setattr(self, "_about_win", None))
        self._about_win = w
        w.show()
        w.raise_()
        w.activateWindow()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        self.open_paths([u.toLocalFile() for u in urls if u.toLocalFile()])

    def closeEvent(self, e):
        # as janelas do esquema e da sucata não são filhas da principal: fecham junto aqui
        for w in (self.pdf_win, self.donor_win, getattr(self, "_about_win", None)):
            if w is not None:
                w.close()
        self.save_session()
        save_prefs(self.settings, self.prefs)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        if self.donor_index is not None:
            self.donor_index.stop()              # para o processo do índice de peças
        super().closeEvent(e)


def _settings_key(path):
    """Chave do QSettings para um caminho de arquivo (sem barras, que o QSettings trata como grupos)."""
    import hashlib
    return hashlib.sha1(os.path.abspath(path).lower().encode("utf-8")).hexdigest()


def apply_dark(app: QApplication):
    app.setStyle("Fusion")
    pal = QPalette()
    base = QColor("#15181e")
    win = QColor("#1b1f26")
    txt = QColor("#e6e6e6")
    pal.setColor(QPalette.Window, win)
    pal.setColor(QPalette.WindowText, txt)
    pal.setColor(QPalette.Base, base)
    pal.setColor(QPalette.AlternateBase, QColor("#1f232b"))
    pal.setColor(QPalette.ToolTipBase, QColor("#262b34"))
    pal.setColor(QPalette.ToolTipText, txt)
    pal.setColor(QPalette.Text, txt)
    pal.setColor(QPalette.Button, QColor("#232832"))
    pal.setColor(QPalette.ButtonText, txt)
    pal.setColor(QPalette.Highlight, QColor("#e3b341"))
    pal.setColor(QPalette.HighlightedText, QColor("#101216"))
    pal.setColor(QPalette.Link, QColor("#6cb6ff"))
    pal.setColor(QPalette.PlaceholderText, QColor("#7b8494"))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        pal.setColor(QPalette.Disabled, role, QColor("#6b7280"))
    app.setPalette(pal)
    app.setStyleSheet("""
        QToolBar { border: none; spacing: 4px; padding: 3px; }
        QLineEdit { padding: 4px 6px; border: 1px solid #2c323d; border-radius: 4px; background: #13161b; }
        QLineEdit:focus { border-color: #e3b341; }
        QDockWidget::title { padding: 4px; background: #20252d; }
        QHeaderView::section { background: #20252d; padding: 4px; border: none; border-right: 1px solid #2c323d; }
        QTableWidget { gridline-color: #262b34; }
        QTabBar::tab { padding: 5px 12px; }
        QStatusBar QLabel { padding: 0 6px; }
    """)


def load_prefs(settings):
    p = dict(DEFAULT_PREFS)
    for k, v in DEFAULT_PREFS.items():
        val = settings.value("prefs/" + k, None)
        if val is None:
            continue
        if isinstance(v, bool):
            p[k] = val if isinstance(val, bool) else str(val).lower() in ("true", "1")
        elif isinstance(v, float):
            p[k] = float(val)
        elif isinstance(v, int):
            p[k] = int(val)
        else:
            p[k] = str(val)
    return p


def save_prefs(settings, prefs):
    for k, v in prefs.items():
        settings.setValue("prefs/" + k, v)


def main():
    os.environ.setdefault("PYOPENGL_ERROR_CHECKING", "0")
    from PySide6.QtGui import QSurfaceFormat
    settings = QSettings(ORG, APP_NAME)
    prefs = load_prefs(settings)
    sc = int(prefs.get("ui_scale", 100) or 100)
    if sc != 100 and "QT_SCALE_FACTOR" not in os.environ:
        os.environ["QT_SCALE_FACTOR"] = f"{sc / 100:.2f}"          # tamanho da interface
    if prefs["renderer"] == "gpu" and gl_available():
        QSurfaceFormat.setDefaultFormat(default_format(prefs["vsync"], prefs["msaa"]))
    argv = list(sys.argv)
    if sys.platform.startswith("linux"):
        # Linux: a janela se identifica como "gdtech-boardview" (WM_CLASS), para o painel juntar com o atalho
        # do menu e mostrar o ícone do programa em vez do do Python
        argv[0] = "gdtech-boardview"
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    if sys.platform.startswith("linux"):
        app.setDesktopFileName("gdtech-boardview")
    try:
        from .icon_data import app_icon
        app.setWindowIcon(app_icon())
    except Exception:  # noqa
        pass
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    # já tem uma janela aberta? manda os arquivos para ela (abrem em abas) e sai
    try:
        from .instance import send_to_running, InstanceServer
        if send_to_running(files):
            return
        server = InstanceServer(app)
    except Exception:  # noqa — sem QtNetwork: segue como antes
        server = None
    apply_dark(app)
    w = MainWindow()
    w.show()
    if server is not None:
        server.filesReceived.connect(lambda paths: (w.open_paths(paths), w.showNormal() if w.isMinimized() else None,
                                                    w.raise_(), w.activateWindow()))
    if files:
        w.open_paths(files)
    elif w.prefs.get("reopen_last_tabs", False):
        w.restore_session()
    # o programa sempre abre na tela de Início; as dicas só aparecem sozinhas se a pessoa ligar
    if w.prefs.get("tips_on_start", False):
        QTimer.singleShot(700, w.show_tips)
    QTimer.singleShot(15000, w._donor_auto)          # índice de peças em segundo plano (só o que mudou)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
