"""Notas/favoritos por placa (e os capacitores já verificados no caçar curto), salvos em JSON na pasta
de dados do usuário. A chave é o nome do arquivo + tamanho, então a nota continua valendo se o arquivo
for movido de pasta."""
from __future__ import annotations

import hashlib
import json
import os
import time

TAGS = [
    ("Trocado", (60, 200, 110, 255)),
    ("Suspeito", (255, 80, 80, 255)),
    ("Verificar", (255, 190, 40, 255)),
    ("OK", (90, 160, 255, 255)),
    ("Nota", (200, 200, 210, 255)),
]
TAG_COLORS = dict(TAGS)


def hexcolor(c):
    return "#%02X%02X%02X" % (c[0], c[1], c[2])


class NoteStore:
    def __init__(self, base_dir, board_path):
        self.board_path = board_path
        try:
            size = os.path.getsize(board_path)
        except OSError:
            size = 0
        key = hashlib.sha1(f"{os.path.basename(board_path).lower()}|{size}".encode("utf-8")).hexdigest()[:20]
        self.dir = os.path.join(base_dir, "notas")
        self.file = os.path.join(self.dir, key + ".json")
        self.notes: dict[str, dict] = {}      # nome do componente -> {"tag", "text", "time"}
        self.checked: set[str] = set()        # capacitores marcados como verificados
        self.load()

    def load(self):
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.notes = {k: v for k, v in d.get("notes", {}).items() if isinstance(v, dict)}
            self.checked = set(d.get("checked", []))
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(self.dir, exist_ok=True)
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"board": os.path.basename(self.board_path), "notes": self.notes,
                           "checked": sorted(self.checked)}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
        except OSError:
            pass

    def set(self, part, tag, text=""):
        self.notes[part.upper()] = {"tag": tag, "text": text, "time": time.strftime("%Y-%m-%d %H:%M")}
        self.save()

    def remove(self, part):
        self.notes.pop(part.upper(), None)
        self.save()

    def get(self, part):
        return self.notes.get(part.upper())

    def lookup(self, part):
        """(etiqueta, texto, cor css) para a caixa de informações."""
        n = self.get(part)
        if not n:
            return None
        return n.get("tag", "Nota"), n.get("text", ""), hexcolor(TAG_COLORS.get(n.get("tag"), TAGS[-1][1]))

    def marks(self, board):
        """componente -> cor, para desenhar na placa."""
        out = {}
        for name, n in self.notes.items():
            pi = board.part_index.get(name)
            if pi is not None:
                out[pi] = TAG_COLORS.get(n.get("tag"), TAGS[-1][1])
        return out

    def set_checked(self, part, on):
        (self.checked.add if on else self.checked.discard)(part.upper())
        self.save()

    def is_checked(self, part):
        return part.upper() in self.checked

    def clear_checked(self):
        self.checked.clear()
        self.save()
