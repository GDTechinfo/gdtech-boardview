"""GDTech BoardView — ponto de entrada.

Instalado pelo INSTALAR.bat, o programa roda sem janela de console (pythonw). Se algo der errado ao abrir,
o erro vai para %LOCALAPPDATA%\\GDTech BoardView\\erro.log e aparece numa caixa de mensagem."""
import os
import sys
import traceback


def _log_dir():
    base = (os.environ.get("LOCALAPPDATA") if sys.platform == "win32" else
            os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")) or os.path.expanduser("~")
    d = os.path.join(base, "GDTech BoardView")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def _show_error(text):
    log = os.path.join(_log_dir(), "erro.log")
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass
    msg = ("O GDTech BoardView encontrou um erro e precisou fechar.\n\n"
           f"Detalhes salvos em:\n{log}\n\n"
           "Se o erro continuar, rode o INSTALAR.bat de novo (ele conserta a instalação).\n\n" + text[-1500:])
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(None, "GDTech BoardView", msg)
        return
    except Exception:  # noqa
        pass
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "GDTech BoardView", 0x10)
            return
        except Exception:  # noqa
            pass
    print(msg, file=sys.stderr)


def _run():
    if sys.platform == "win32":
        try:
            import ctypes
            # ícone próprio na barra de tarefas (em vez do ícone do Python)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GDTechInformatica.BoardView")
        except Exception:  # noqa
            pass
    # pythonw não tem console: print() sem destino não pode derrubar o programa
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.path.join(_log_dir(), "saida.log"), "a", encoding="utf-8", errors="replace")
    from gdboard.main import main
    main()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()      # o índice de peças roda num processo à parte (inclusive no .exe)
    try:
        _run()
    except SystemExit:
        raise
    except BaseException:  # noqa
        _show_error(traceback.format_exc())
        sys.exit(1)
