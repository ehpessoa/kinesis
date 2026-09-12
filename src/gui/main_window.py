"""Janela principal PyQt6: carrega o dashboard HTML/CSS/JS local via
QWebEngineView (file://, sem servidor HTTP) e liga o GuiBridge via
QWebChannel. Um QTimer dirige o laço de captura/análise/eventos, mantendo a
interface Qt responsiva (nada de loop bloqueante como o `while True` do
main.py em modo linha de comando)."""
import os

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QMainWindow

from src.gui.bridge import GuiBridge

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
TICK_INTERVAL_MS = 33  # ~30 Hz de laço de captura/análise


class MainWindow(QMainWindow):
    def __init__(self, bridge: GuiBridge):
        super().__init__()
        self.setWindowTitle("Kinesis SMA-TR — Monitoramento Assistencial")
        self.resize(1360, 860)

        self.bridge = bridge
        self.view = QWebEngineView()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)
        self.view.load(QUrl.fromLocalFile(os.path.join(WEB_DIR, "index.html")))
        self.setCentralWidget(self.view)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.bridge.tick)
        self.timer.start(TICK_INTERVAL_MS)

        self.bridge.requestQuit.connect(self.close)

    def closeEvent(self, event):
        self.timer.stop()
        self.bridge.shutdown()
        super().closeEvent(event)
