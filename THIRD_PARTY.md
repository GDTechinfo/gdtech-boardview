# Créditos e licenças de terceiros

O GDTech BoardView é distribuído sob a licença MIT (veja [LICENSE](LICENSE)). Ele usa ou se baseia nos
trabalhos abaixo, cada um com a sua licença.

## Código de onde partes deste programa foram portadas

### OpenBoardView — licença MIT

Os leitores de formato em `gdboard/parsers.py` (BRD, BRD2, BDV, ASC, BVR, CAD/GenCAD, CST, FZ, CAE, XZZPCB)
foram portados para Python a partir do código C++ do [OpenBoardView](https://github.com/OpenBoardView/OpenBoardView).

Texto original: <https://github.com/OpenBoardView/OpenBoardView/blob/master/LICENSE>

```
Copyright (c) the OpenBoardView authors and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Bibliotecas usadas (não fazem parte deste repositório; são baixadas na instalação ou embutidas nos executáveis)

| Biblioteca | Para quê | Licença |
|---|---|---|
| [Qt for Python (PySide6)](https://www.qt.io/qt-for-python) / Qt 6 | interface | LGPL v3 |
| [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) | esquema em PDF | Apache 2.0 / BSD-3-Clause |
| [PDFium](https://pdfium.googlesource.com/pdfium/) (dentro do pypdfium2) | esquema em PDF | BSD-3-Clause / Apache 2.0 |
| [NumPy](https://numpy.org/) | contas da placa | BSD-3-Clause |
| [PyOpenGL](https://github.com/mcfletch/pyopengl) | desenho pela placa de vídeo | BSD-3-Clause |
| [PyCryptodome](https://www.pycryptodome.org/) | arquivos FZ/CAE (descompactar/decifrar) | BSD-2-Clause / domínio público |
| [Python](https://www.python.org/) | — | PSF License |

O Qt é usado como biblioteca dinâmica, sem modificações. No executável (.exe / .AppImage) as bibliotecas do Qt
vão junto em arquivos separados; quem quiser pode trocar por outra versão compatível, como pede a LGPL. O
código-fonte do Qt está em <https://download.qt.io/>.
