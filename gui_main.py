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
from main import CameraPipeline, NotificationDispatcher, create_voice_controllers
from src.gui.bridge import GuiBridge
from src.gui.main_window import MainWindow
from src.monitoring.checkin import CheckinScheduler
from src.monitoring.status import build_status
from src.notifications.whatsapp_client import WhatsAppNotifier
from src.server.remote_server import RemoteStatusServer
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
            night_routine_config=app_config.night_routine,
            seizure_config=app_config.seizure_detection,
        )
        for cam in app_config.cameras
    ]

    event_logger = EventLogger()
    if app_config.storage.retention_hours > 0:
        event_logger.start_auto_purge(
            retention_hours=app_config.storage.retention_hours,
            check_interval_seconds=app_config.storage.purge_interval_minutes * 60.0,
        )

    checkin_scheduler = CheckinScheduler(app_config, contacts, dispatcher, event_logger=event_logger)
    checkin_scheduler.start()

    voice_controllers = create_voice_controllers(app_config, contacts, dispatcher, event_logger)

    remote_server = None
    if app_config.remote_server.enabled:
        remote_server = RemoteStatusServer(
            host=app_config.remote_server.host,
            port=app_config.remote_server.port,
            token=app_config.remote_server.token,
            status_provider=lambda: build_status(pipelines, app_config, voice_controllers),
            history_provider=lambda limit: event_logger.read_recent(limit=limit),
            snapshot_fps=app_config.remote_server.snapshot_fps,
        )
        remote_server.start()

    qt_app = QApplication(sys.argv)
    bridge = GuiBridge(pipelines, app_config, contacts, notifier, dispatcher,
                        event_logger=event_logger, voice_controllers=voice_controllers,
                        checkin_scheduler=checkin_scheduler, remote_server=remote_server)
    window = MainWindow(bridge)
    window.show()
    sys.exit(qt_app.exec())


if __name__ == "__main__":
    main()
