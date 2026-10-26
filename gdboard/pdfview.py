"""Visualizador do esquema elétrico (PDF), ligado à placa.

Usa o pdfium (pacote pypdfium2): desenho nítido na escala da tela, seleção de texto e busca centralizada.
Sem o pypdfium2, cai no visualizador antigo do QtPdf."""
try:
    from .pdfview_pdfium import SchematicWindow, PdfCanvas  # noqa: F401
    ENGINE = "pdfium"
except ImportError:
    from .pdfview_qt import SchematicWindow  # noqa: F401
    ENGINE = "qtpdf"
