"""Dicas da primeira vez (e do menu Ajuda → Dicas)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox

PAGES = [
    ("Bem-vindo ao GDTech BoardView",
     "<p>Abra boardviews pelo botão <b>Abrir</b>, arrastando os arquivos para a janela ou pela tela de "
     "<b>Início</b> (recentes e biblioteca).</p>"
     "<p>Cada arquivo abre numa <b>aba</b>, ao lado da aba fixa <b>Início</b> (volte nela com <b>Ctrl+T</b> para "
     "abrir outra placa sem fechar a atual). <b>Ctrl+W</b> fecha, <b>Ctrl+Tab</b> troca. "
     "O programa sempre abre na tela de <b>Início</b>, com os arquivos recentes a um clique.</p>"
     "<p>Na <b>Biblioteca</b> (tela de Início), adicione a pasta dos seus boardviews: o programa indexa tudo e "
     "acha o <b>PDF do esquema</b> de cada placa sozinho.</p>"),
    ("Mexendo na placa",
     "<p><b>Roda do mouse</b>: zoom no cursor · <b>arrastar</b>: mover · <b>clique</b>: seleciona · "
     "<b>duplo clique</b>: mostra o componente no esquema (sem mexer no zoom).</p>"
     "<p><b>Espaço</b> vira o lado (Top/Bottom) · <b>R</b> gira · <b>F</b> ajusta à tela · "
     "<b>Ctrl+F</b> busca componente (caixa <b>Part</b>), <b>Ctrl+Shift+F</b> busca rede (caixa <b>Net</b>).</p>"
     "<p>Com uma rede selecionada, <b>Tab</b> / <b>Shift+Tab</b> vão de pino em pino da mesma rede.</p>"
     "<p>O <b>minimapa</b> no canto (F7) mostra onde você está; clique nele para ir até lá.</p>"),
    ("Ver os dois lados e as camadas",
     "<p><b>F2</b> — vista dupla: o outro lado aparece espelhado ao lado da placa, como um livro aberto, e as "
     "linhas da rede ligam as duas.</p>"
     "<p><b>F3</b> — modo camadas (.tvw e .cad): trilhas, planos de cobre e vias de cada camada; "
     "<b>PgUp / PgDn</b> passam uma camada por vez.</p>"
     "<p><b>F8</b> destaca pinos soltos e componentes sem rede. O GND selecionado fica só com contorno, "
     "para não acender a placa inteira.</p>"),
    ("Ferramentas de conserto (F6)",
     "<p><b>Caçar curto</b>: clique num pino da rede em curto e use <b>Ctrl+K</b>. A lista traz todos os "
     "capacitores da rede; <b>]</b> / <b>[</b> próximo/anterior e <b>V</b> marca como verificado.</p>"
     "<p><b>Seguir a rede</b> (Ctrl+L): mostra o caminho da alimentação por resistores de 0 Ω, indutores e "
     "fusíveis.</p>"
     "<p><b>Busca</b> na caixa Part: <b>U5*</b>, <b>0.47uF</b>, <b>10k</b>, <b>0402</b> — tudo aceso de uma vez. "
     "Na caixa Net: nome, parte do nome ou <b>*VDD*</b> — vai até um pino da rede; Enter de novo, próximo pino.</p>"
     "<p><b>Comparar</b> duas revisões da mesma placa.</p>"
     "<p><b>Sucata</b>: botão direito num componente → <b>Procurar esta peça em outras placas</b> "
     "(Ctrl+Shift+P). Acha o mesmo componente em todos os boardviews e esquemas da Biblioteca.</p>"),
    ("Esquema, notas e datasheet",
     "<p><b>F4</b> abre o PDF do esquema. Com ele aberto, <b>duplo clique</b> num componente vai até ele no "
     "esquema.</p>"
     "<p>No esquema: <b>arrastar em cima do texto</b> seleciona (<b>Ctrl+C</b> copia), arrastar fora do texto "
     "move. <b>Botão direito</b> num nome → <b>Mostrar na placa</b>.</p>"
     "<p><b>Botão direito</b> num componente: marcar como Trocado / Suspeito / Verificar / OK, escrever uma "
     "nota, procurar o datasheet ou copiar o nome.</p>"),
    ("Do seu jeito",
     "<p><b>Tema</b>: vários prontos (inclusive Verde Escuro e Algodão-doce), editor de cores e importação "
     "de arquivos .theme.</p>"
     "<p><b>Ferramentas → Atalhos</b>: troque qualquer tecla ou botão do mouse.</p>"
     "<p><b>Ferramentas → Preferências</b>: tamanho da interface, tamanho dos pinos e textos, suavidade, "
     "VSync e mais.</p>"
     "<p>Estas dicas ficam em <b>Ajuda → Dicas</b>.</p>"),
]


class TipsDialog(QDialog):
    def __init__(self, parent=None, show_on_start=True):
        super().__init__(parent)
        self.setWindowTitle("Dicas")
        self.setMinimumSize(560, 360)
        self.k = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 14)
        self.step = QLabel("")
        self.step.setStyleSheet("color: #7d8696;")
        self.title = QLabel("")
        self.title.setStyleSheet("font-size: 18px; font-weight: 700; color: #e3b341;")
        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.RichText)
        self.body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.body.setStyleSheet("font-size: 13px; line-height: 140%;")
        lay.addWidget(self.step)
        lay.addWidget(self.title)
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        self.again = QCheckBox("Mostrar as dicas ao abrir o programa")
        self.again.setChecked(show_on_start)
        self.prev = QPushButton("◀ Anterior")
        self.next = QPushButton("Próxima ▶")
        self.close_b = QPushButton("Fechar")
        self.prev.clicked.connect(lambda: self.go(self.k - 1))
        self.next.clicked.connect(lambda: self.go(self.k + 1))
        self.close_b.clicked.connect(self.accept)
        row.addWidget(self.again, 1)
        row.addWidget(self.prev)
        row.addWidget(self.next)
        row.addWidget(self.close_b)
        lay.addLayout(row)
        self.go(0)

    def go(self, k):
        self.k = max(0, min(k, len(PAGES) - 1))
        t, b = PAGES[self.k]
        self.step.setText(f"Dica {self.k + 1} de {len(PAGES)}")
        self.title.setText(t)
        self.body.setText(b)
        self.prev.setEnabled(self.k > 0)
        self.next.setEnabled(self.k < len(PAGES) - 1)
        (self.next if self.k < len(PAGES) - 1 else self.close_b).setDefault(True)
