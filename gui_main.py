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
from src.storage.event_log import EventLogger
from src.vision.detectors import ensure_models_downloaded


def main():
    ensure_models_downloaded()

    app_config = load_app_config()
    contacts = load_contacts()
    notifier = WhatsAppNotifier(app_config.whatsapp)
    dispatcher = NotificationDispatcher(app_config, contacts, notifier)
    dispatcher.redeliver_pending()

    object_detector = None
    if app_config.object_detection.enabled:
        from src.vision.object_detector import MobilityAidDetector
        print("Carregando detector de dispositivos de mobilidade (YOLO-World)... pode levar um tempo na 1a execucao.")
        object_detector = MobilityAidDetector(confidence=app_config.object_detection.confidence)

    pipelines = [
        CameraPipeline(
            name=cam.name, src=cam.resolve_src(),
            object_detector=object_detector,
            object_detect_interval=app_config.object_detection.frame_interval,
            person_tracking_enabled=app_config.person_tracking.enabled,
            person_tracking_confidence=app_config.person_tracking.confidence,
            person_tracking_interval=app_config.person_tracking.frame_interval,
            pose_fps=app_config.pipeline_rates.pose_fps,
            face_fps=app_config.pipeline_rates.face_fps,
            gesture_fps=app_config.pipeline_rates.gesture_fps,
        )
        for cam in app_config.cameras
    ]

    event_logger = EventLogger()

    qt_app = QApplication(sys.argv)
    bridge = GuiBridge(pipelines, app_config, contacts, notifier, dispatcher, event_logger=event_logger)
    window = MainWindow(bridge)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
