"""Ponto de entrada da GUI Desktop Híbrida (PyQt6 + QWebEngineView).

Alternativa a `main.py` (que roda em janelas cv2.imshow, útil para depuração
sem GUI): aqui a apresentação é um dashboard HTML/CSS/JS local carregado via
file://, comunicando com o backend Python por QWebChannel — sem nenhum
servidor HTTP. Reaproveita `CameraPipeline`/`NotificationDispatcher` de
main.py para não duplicar a lógica de captura/visão/eventos.
"""
import sys

from PyQt6.QtWidgets import QApplication

from config.loader import load_app_config, load_contacts
from main import CameraPipeline, NotificationDispatcher
from src.gui.bridge import GuiBridge
from src.gui.main_window import MainWindow
from src.notifications.whatsapp_client import WhatsAppNotifier
from src.vision.detectors import ensure_models_downloaded


def main():
    ensure_models_downloaded()

    app_config = load_app_config()
    contacts = load_contacts()
    notifier = WhatsAppNotifier(app_config.whatsapp)
    dispatcher = NotificationDispatcher(app_config, contacts, notifier)
    pipelines = [CameraPipeline(name=cam.name, src=cam.resolve_src()) for cam in app_config.cameras]

    qt_app = QApplication(sys.argv)
    bridge = GuiBridge(pipelines, app_config, contacts, notifier, dispatcher)
    window = MainWindow(bridge)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
