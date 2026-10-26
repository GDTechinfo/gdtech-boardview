"""Índice de peças num PROCESSO separado (não numa thread).

Motivo: no Python, threads do mesmo processo não rodam código Python ao mesmo tempo (GIL). Ler centenas de
boardviews numa thread disputava a vez com a interface e a placa dava "engasgos". Num processo à parte o
índice roda em outro núcleo do processador, com prioridade baixa, e a interface fica livre.

O processo filho só manda mensagens de progresso por uma fila: ("p", feitos, total, arquivo) e ("d", placas, pdfs).
Este módulo não usa Qt."""
from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import threading


def _low_priority():
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL = 0x00004000
            PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000      # também deixa o disco em prioridade baixa
            k = ctypes.windll.kernel32
            h = k.GetCurrentProcess()
            k.SetPriorityClass(h, BELOW_NORMAL)
            k.SetPriorityClass(h, PROCESS_MODE_BACKGROUND_BEGIN)
        else:
            os.nice(10)
    except Exception:  # noqa
        pass


def _child(db_path, roots, keys, q, stop_ev):
    """Roda no processo filho."""
    # aberto pelo pythonw (sem console): print/traceback não podem ir para "lugar nenhum"
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    _low_priority()
    try:
        from . import donor
        ix = donor.Indexer(db_path, roots, keys,
                           progress=lambda k, n, p: q.put(("p", k, n, p)),
                           done=lambda b, p: q.put(("d", b, p)),
                           should_stop=stop_ev.is_set)
        ix.run()                      # no próprio processo (sem thread extra)
    except Exception:  # noqa
        import traceback
        try:
            base = (os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else
                    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")) or os.path.expanduser("~")
            os.makedirs(os.path.join(base, "GDTech BoardView"), exist_ok=True)
            with open(os.path.join(base, "GDTech BoardView", "erro.log"), "a", encoding="utf-8") as f:
                f.write("[índice de peças]\n" + traceback.format_exc() + "\n")
        except OSError:
            pass


class Handle:
    """Processo de índice em andamento. on_progress(k, n, arquivo) e on_done(placas, pdfs) são chamados numa
    thread de leitura da fila (a janela repassa para a interface por sinal)."""

    def __init__(self, db_path, roots, keys, on_progress=None, on_done=None):
        ctx = mp.get_context("spawn")           # igual no Windows e no Linux; seguro com Qt aberto
        self.q = ctx.Queue()
        self.stop_ev = ctx.Event()
        self.db_path = db_path
        self.on_progress = on_progress or (lambda *a: None)
        self.on_done = on_done or (lambda *a: None)
        self.proc = ctx.Process(target=_child, args=(db_path, list(roots), keys or {}, self.q, self.stop_ev),
                                daemon=True, name="indice-pecas")
        self.proc.start()
        self._reader = threading.Thread(target=self._read, daemon=True, name="indice-pecas-fila")
        self._reader.start()

    def _read(self):
        finished = False
        while not finished:
            try:
                msg = self.q.get(timeout=0.5)
            except queue.Empty:
                if not self.proc.is_alive():
                    break                        # o filho morreu sem avisar (erro): fecha assim mesmo
                continue
            except (EOFError, OSError):
                break
            if msg[0] == "p":
                self.on_progress(msg[1], msg[2], msg[3])
            elif msg[0] == "d":
                finished = True
                self.on_done(msg[1], msg[2])
        if not finished:
            try:
                from . import donor
                con = donor.connect(self.db_path)
                b, p = donor.stats(con)
                con.close()
            except Exception:  # noqa
                b = p = 0
            self.on_done(b, p)
        self.proc.join(5)

    def alive(self):
        return self.proc.is_alive() or self._reader.is_alive()

    def stop(self, wait=0.0):
        self.stop_ev.set()
        if wait:
            self.proc.join(wait)
            if self.proc.is_alive():
                self.proc.terminate()


def start(db_path, roots, keys=None, on_progress=None, on_done=None):
    return Handle(db_path, roots, keys, on_progress, on_done)
