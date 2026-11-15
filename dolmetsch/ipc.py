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


def send(command: str, name: str = NAME, timeout: int = 300) -> bool:
    """Передать команду уже запущенной программе. False — программа не запущена."""
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(timeout):
        return False
    sock.write(command.encode("utf-8"))
    sock.waitForBytesWritten(timeout)
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

        def read() -> None:
            command = bytes(sock.readAll()).decode("utf-8", "replace").strip()
            if command in COMMANDS:
                handler(command)
        sock.readyRead.connect(read)
        sock.disconnected.connect(sock.deleteLater)
        if sock.bytesAvailable():
            read()
    server.newConnection.connect(accept)
    server.listen(name)
    return server
