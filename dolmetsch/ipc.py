"""Один экземпляр программы и команды снаружи.

Второй запуск Dolmetsch.exe не открывает вторую копию, а передаёт команду первой:
    Dolmetsch.exe            показать окно
    Dolmetsch.exe --toggle   старт / стоп перевода (так Dolmetsch запускает Capri своей клавишей)
    Dolmetsch.exe --start    начать перевод
    Dolmetsch.exe --stop     остановить перевод
"""
from __future__ import annotations

from PySide6.QtNetwork import QLocalServer, QLocalSocket

NAME = "dolmetsch-ipc"
COMMANDS = ("show", "toggle", "start", "stop")


def send(command: str, name: str = NAME, timeout: int = 1500) -> bool:
    """Передать команду уже запущенной программе. False — программа не запущена."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(300):
        return False
    sock.write(command.encode("utf-8"))
    sock.waitForBytesWritten(timeout)
    sock.waitForReadyRead(timeout)           # ждём «ok»: без этого на Windows команда может потеряться при закрытии
    sock.disconnectFromServer()
    return True


def listen(handler, name: str = NAME) -> QLocalServer:
    """Слушать команды от следующих запусков. handler(команда) вызывается в главном потоке."""
    QLocalServer.removeServer(name)          # остаток после аварийного завершения
    server = QLocalServer()

    def accept() -> None:
        sock = server.nextPendingConnection()
        if sock is None:
            return
        if sock.bytesAvailable() or sock.waitForReadyRead(500):
            command = bytes(sock.readAll()).decode("utf-8", "replace").strip()
            sock.write(b"ok")
            sock.flush()
            sock.waitForBytesWritten(300)
            if command in COMMANDS:          # чужие строки не выполняются
                handler(command)
        sock.disconnectFromServer()
        sock.deleteLater()
    server.newConnection.connect(accept)
    server.listen(name)
    return server
