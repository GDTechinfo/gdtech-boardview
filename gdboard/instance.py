"""Uma janela só: abrir um boardview com o programa já aberto (duplo clique no arquivo) manda o arquivo para
a janela que já existe, que abre numa aba nova."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "GDTechBoardView-instancia"


def send_to_running(paths) -> bool:
    """Se o programa já está aberto, manda os arquivos para ele e devolve True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(300):
        return False
    msg = "\n".join(os.path.abspath(p) for p in paths) or "\n"
    sock.write(msg.encode("utf-8") + b"\0")
    sock.flush()
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    filesReceived = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)       # sobra de um programa que fechou mal
        self.server.listen(SERVER_NAME)
        self.server.newConnection.connect(self._conn)

    def _conn(self):
        s = self.server.nextPendingConnection()
        if s is None:
            return
        buf = bytearray()

        def read():
            buf.extend(bytes(s.readAll()))
            if buf.endswith(b"\0"):
                paths = [p for p in buf[:-1].decode("utf-8", "replace").split("\n") if p.strip()]
                self.filesReceived.emit(paths)
                s.disconnectFromServer()
        s.readyRead.connect(read)
        s.disconnected.connect(s.deleteLater)
