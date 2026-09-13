"""Ponto de entrada do SMA-TR: orquestra N CameraPipeline (uma por fonte de
câmera configurada em config/config.json), cada uma com seus próprios
detectores MediaPipe, BehaviorTracker e EventEngine, e despacha as
notificações de evento via WhatsApp conforme a matriz de eventos configurada.

GUI (PyQt6/QWebEngine) e módulo de voz (VAD/Whisper/Piper) ainda não foram
portados para esta base — a apresentação continua em janelas OpenCV.
"""
import threading
import time
from typing import Optional

import cv2
import mediapipe as mp

from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    FaceLandmarker,
    FaceLandmarkerOptions,
    GestureRecognizer,
    GestureRecognizerOptions,
    PoseLandmarker,
    PoseLandmarkerOptions,
    RunningMode,
)

from config.loader import load_app_config, load_contacts
from config.schemas import AppConfig, ContactsFile
from src.behavior.event_engine import EventEngine, EventNotification
from src.behavior.tracker import BehaviorTracker
from src.capture.threaded_camera import ThreadedCamera
from src.monitoring.checkin import CheckinScheduler
from src.monitoring.status import build_status
from src.notifications.whatsapp_client import WhatsAppNotifier, encode_frame_jpeg_base64, encode_frame_jpeg_bytes
from src.server.remote_server import RemoteStatusServer
from src.vision.detectors import (
    FACE_CONNECTIONS,
    FACE_MODEL_PATH,
    GESTURE_MODEL_PATH,
    HAND_CONNECTIONS,
    POSE_CONNECTIONS,
    POSE_MODEL_PATH,
    draw_landmarks,
    ensure_models_downloaded,
)
from src.vision.rate_limiter import RateLimiter
from src.storage.event_log import EventLogger
from src.storage.notification_queue import PendingNotificationQueue


class NotificationDispatcher:
    """Encaminha EventNotification para os contatos configurados via WhatsApp,
    respeitando a matriz de eventos (habilitado/desabilitado, destinatários).

    Cada notificação é persistida em `PendingNotificationQueue` ANTES da
    tentativa de envio (que roda em thread separada, com retry/backoff em
    memória — ver WhatsAppNotifier) e só é removida da fila após confirmação
    de entrega. Isso garante que uma queda do processo a qualquer momento
    (durante o backoff, ou após esgotar as tentativas) não perca o registro
    da notificação — ela fica pronta para reenvio via `redeliver_pending()`
    na próxima inicialização."""

    def __init__(
        self, config: AppConfig, contacts: ContactsFile, notifier: WhatsAppNotifier,
        queue: Optional[PendingNotificationQueue] = None,
    ):
        self.config = config
        self.contacts = contacts
        self.notifier = notifier
        self.queue = queue or PendingNotificationQueue()

    def dispatch(self, event: EventNotification, frame_b64: Optional[str]):
        rule = self.config.events.get(event.event_id)
        if rule is None or not rule.enabled:
            return
        if rule.severity_override:
            event = event.model_copy(update={"severity": rule.severity_override})

        for contact_id in rule.notify_contact_ids:
            contact = self.contacts.find(contact_id)
            if contact is None or not contact.whatsapp_number:
                continue
            record_id = self.queue.enqueue(event.model_dump(mode="json"), contact.whatsapp_number, frame_b64)
            threading.Thread(
                target=self._send_and_finalize,
                args=(record_id, event, contact.whatsapp_number, frame_b64),
                daemon=True,
            ).start()

    def dispatch_to_contact(self, event: EventNotification, contact_id: str, frame_b64: Optional[str] = None) -> bool:
        """Envia diretamente a UM contato especifico, sem passar pelo
        fan-out para os `notify_contact_ids` configurados do evento (quem
        deve receber ja e conhecido - ex: modulo de voz com "chama o
        Carlos"). Ainda assim respeita a flag `enabled` da matriz `events`,
        se houver uma regra configurada para este event_id - do contrario
        desabilitar um evento na GUI nao teria efeito sobre este caminho."""
        rule = self.config.events.get(event.event_id)
        if rule is not None and not rule.enabled:
            return False
        if rule is not None and rule.severity_override:
            event = event.model_copy(update={"severity": rule.severity_override})

        contact = self.contacts.find(contact_id)
        if contact is None or not contact.whatsapp_number:
            return False
        record_id = self.queue.enqueue(event.model_dump(mode="json"), contact.whatsapp_number, frame_b64)
        threading.Thread(
            target=self._send_and_finalize,
            args=(record_id, event, contact.whatsapp_number, frame_b64),
            daemon=True,
        ).start()
        return True

    def _send_and_finalize(self, record_id: str, event: EventNotification, recipient_number: str, frame_b64: Optional[str]):
        if self.notifier.send_event(event, recipient_number, frame_b64):
            self.queue.mark_delivered(record_id)
        # Falha apos esgotar as tentativas: o registro permanece na fila,
        # tratado como pendente (nao ha alerta adicional aqui de proposito -
        # WhatsAppNotifier.send_event ja loga o erro; ver redeliver_pending).

    def redeliver_pending(self):
        """Reenvia notificacoes que ficaram pendentes de uma execucao
        anterior (processo encerrado/crashado antes da confirmacao de
        entrega). Chamado no startup, antes do laco principal."""
        pending = self.queue.list_pending()
        if not pending:
            return
        print(f"Reenviando {len(pending)} notificacao(oes) WhatsApp pendente(s) de uma execucao anterior...")
        for record_id, record in pending.items():
            event = EventNotification.model_validate(record["event"])
            threading.Thread(
                target=self._send_and_finalize,
                args=(record_id, event, record["recipient_number"], record.get("frame_b64")),
                daemon=True,
            ).start()


class CameraPipeline:
    """Tudo que é específico de UMA fonte de vídeo: captura, detectores
    MediaPipe (com timeline própria), BehaviorTracker e EventEngine."""

    def __init__(
        self, name: str, src, object_detector=None, object_detect_interval: int = 5,
        person_tracking_enabled: bool = True, person_tracking_confidence: float = 0.4,
        person_tracking_interval: int = 1,
        pose_fps: float = 18.0, face_fps: float = 8.0, gesture_fps: float = 8.0,
        night_routine_config=None, seizure_config=None,
    ):
        self.name = name
        self.cam = ThreadedCamera(src, name=name).start()
        self.tracker = BehaviorTracker()
        self.event_engine = EventEngine(
            source_name=name, night_routine_config=night_routine_config, seizure_config=seizure_config,
        )
        self.last_frame_count = -1
        self.prev_frame_time = time.time()
        self.fps = 0.0

        # Otimizacao de taxa de quadros por sub-pipeline (item 4.1.1 do
        # plano): Pose/Face/Gesture rodam no maximo ao ritmo configurado
        # (Hz), nao a cada frame da camera — ver src/vision/rate_limiter.py.
        # Entre execucoes, o ultimo resultado de cada detector e reaproveitado
        # (landmarks desenhados continuam do ultimo frame processado).
        self.pose_rate = RateLimiter(pose_fps)
        self.face_rate = RateLimiter(face_fps)
        self.gesture_rate = RateLimiter(gesture_fps)
        self._last_pose_landmarks_list = []
        self._last_face_landmarks_list = []
        self._last_hand_landmarks_list = []
        self._last_pose_data = {
            "posture": "N/A", "dynamic_state": "Estatico", "fall_alert": False,
            "arm_raised": False, "hand_near_face": False, "torso_h": 0.0,
        }
        self._last_blend_data = {"emotion": "N/A", "drowsy_alert": False, "distress_score": 0.0}
        self._last_head_pose = "N/A"
        self._last_gestures = {}

        # Detector de dispositivos de mobilidade (bengala/andador/cadeira de
        # rodas): compartilhado entre todas as CameraPipeline (sem estado
        # entre frames, ao contrario dos detectores MediaPipe acima) e
        # rodado a cada N frames (object_detect_interval) para nao pesar a
        # CPU a cada frame — ver src/vision/object_detector.py.
        self.object_detector = object_detector
        self.object_detect_interval = max(1, object_detect_interval)
        self._object_frame_counter = 0
        self._last_mobility_detections = []

        # Rastreamento continuo de pessoas (ByteTrack) — mitigacao do item
        # 4.1.3 do plano. Ao contrario do object_detector acima, o tracker
        # MANTEM estado entre frames (historico de tracks), entao cada
        # CameraPipeline precisa da sua PROPRIA instancia, nunca uma
        # compartilhada — ver src/vision/person_tracker.py.
        self.person_tracker = None
        if person_tracking_enabled:
            from src.vision.person_tracker import PersonTracker
            self.person_tracker = PersonTracker(confidence=person_tracking_confidence)
        self.person_tracking_interval = max(1, person_tracking_interval)
        self._person_frame_counter = 0
        self._last_people = []
        self._last_primary = None

        self.pose_detector = PoseLandmarker.create_from_options(PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=POSE_MODEL_PATH),
            running_mode=RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ))
        self.face_detector = FaceLandmarker.create_from_options(FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=FACE_MODEL_PATH),
            running_mode=RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=True,
        ))
        self.gesture_recognizer = GestureRecognizer.create_from_options(GestureRecognizerOptions(
            base_options=BaseOptions(model_asset_path=GESTURE_MODEL_PATH),
            running_mode=RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        ))

    def process_next_frame(self):
        """Lê o frame mais recente e roda a análise. Retorna None se não há
        frame novo desde a última chamada (evita timestamp duplicado, que o
        MediaPipe rejeita por exigir timestamps estritamente crescentes)."""
        ret, frame, frame_count = self.cam.read()
        if not ret or frame is None or frame_count == self.last_frame_count:
            return None
        self.last_frame_count = frame_count

        if self.cam.is_local_webcam():
            frame = cv2.flip(frame, 1)

        h, w, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        # Timestamp local ao contador de frames desta fonte: monotonico mesmo
        # que a camera fique momentaneamente sem sinal e reconecte.
        timestamp_ms = frame_count
        now = time.time()

        # Cada detector so roda quando seu RateLimiter permite (Hz proprio,
        # ver __init__); nos frames "pulados", reaproveita-se o ultimo
        # resultado (landmarks continuam desenhados, metricas continuam
        # populadas) em vez de re-inferir a cada frame da camera.
        if self.face_rate.should_run(now):
            face_results = self.face_detector.detect_for_video(mp_image, timestamp_ms)
            self._last_face_landmarks_list = face_results.face_landmarks or []
            if face_results.face_landmarks:
                self._last_head_pose = self.tracker.estimate_head_pose(face_results.face_landmarks[0], w, h)
            if face_results.face_blendshapes:
                self._last_blend_data = self.tracker.analyze_blendshapes(face_results.face_blendshapes[0])
        for face_landmarks in self._last_face_landmarks_list:
            draw_landmarks(frame, face_landmarks, FACE_CONNECTIONS, w, h, (0, 255, 255), radius=1)

        if self.pose_rate.should_run(now):
            pose_results = self.pose_detector.detect_for_video(mp_image, timestamp_ms)
            self._last_pose_landmarks_list = pose_results.pose_landmarks or []
            if pose_results.pose_landmarks:
                self._last_pose_data = self.tracker.analyze_pose(pose_results.pose_landmarks[0], w, h)
        for pose_landmarks in self._last_pose_landmarks_list:
            draw_landmarks(frame, pose_landmarks, POSE_CONNECTIONS, w, h, (100, 255, 100), radius=3)

        if self.gesture_rate.should_run(now):
            gesture_results = self.gesture_recognizer.recognize_for_video(mp_image, timestamp_ms)
            self._last_hand_landmarks_list = gesture_results.hand_landmarks or []
            self._last_gestures = (
                self.tracker.analyze_gestures(gesture_results) if gesture_results.hand_landmarks else {}
            )
        for hand_landmarks in self._last_hand_landmarks_list:
            draw_landmarks(frame, hand_landmarks, HAND_CONNECTIONS, w, h, (255, 120, 255), radius=2)

        pose_data = self._last_pose_data
        blend_data = self._last_blend_data
        head_pose = self._last_head_pose
        gestures = self._last_gestures

        # Caixas delimitadoras (objeto/pessoa) NAO sao mais desenhadas nos
        # pixels do frame aqui - ficam disponiveis cruas em metrics["detections"]
        # /["people"] para quem consumir process_next_frame() decidir como
        # exibir (o CLI as queima na imagem em render(), a GUI as desenha como
        # camada HTML/SVG sobre o <canvas> - ver bridge.py e app.js).
        if self.object_detector is not None:
            self._object_frame_counter += 1
            if self._object_frame_counter % self.object_detect_interval == 0:
                self._last_mobility_detections = self.object_detector.detect(frame)

        if self.person_tracker is not None:
            self._person_frame_counter += 1
            if self._person_frame_counter % self.person_tracking_interval == 0:
                self._last_people = self.person_tracker.track(frame)
                self._last_primary = self.person_tracker.pick_primary(self._last_people)

            self.tracker.registered_id = (
                f"Pessoa {self._last_primary['track_id']}" if self._last_primary else "Sem pessoa detectada"
            )

        person_label = self.tracker.registered_id if self.person_tracker is not None else None
        person_point = None
        if self._last_primary is not None:
            x1, y1, x2, y2 = self._last_primary["bbox"]
            # Base da caixa delimitadora (pes), normalizada - mais estavel
            # que o centro da caixa para checar "esta na zona da cama"
            # (EVT-10): o centro de uma pessoa em pe fica bem acima da cama
            # mesmo estando ao lado dela.
            person_point = ((x1 + x2) / 2.0 / w, y2 / h)
        events = self.event_engine.update(
            pose_data, blend_data, now=now, person_label=person_label, person_point=person_point,
        )

        self.fps = 1.0 / max(1e-5, (now - self.prev_frame_time))
        self.prev_frame_time = now

        primary_track_id = self._last_primary["track_id"] if self._last_primary else None
        metrics = {
            "emotion": blend_data["emotion"],
            "head_pose": head_pose,
            "posture": pose_data["posture"],
            "motion": pose_data["dynamic_state"],
            "arm_raised": pose_data["arm_raised"],
            "hand_near_face": pose_data["hand_near_face"],
            "drowsy_alert": blend_data["drowsy_alert"],
            "fall_alert": pose_data["fall_alert"],
            "gestures": gestures,
            "events": events,
            "mobility_aids": sorted({d["label"] for d in self._last_mobility_detections}),
            "person_count": len(self._last_people),
            "frame_size": [w, h],
            "detections": [
                {"label": d["label"], "confidence": d["confidence"], "bbox": list(d["bbox"])}
                for d in self._last_mobility_detections
            ],
            "people": [
                {
                    "track_id": p["track_id"], "confidence": p["confidence"], "bbox": list(p["bbox"]),
                    "is_primary": p["track_id"] == primary_track_id,
                }
                for p in self._last_people
            ],
        }
        return frame, metrics

    def render(self, frame, metrics):
        h, w, _ = frame.shape
        gestures_text = ", ".join(f"{side}: {label}" for side, label in metrics["gestures"].items()) or "Nenhum"
        mobility_text = ", ".join(metrics["mobility_aids"]) or "Nenhum"

        if metrics["detections"]:
            from src.vision.object_detector import draw_object_detections
            draw_object_detections(frame, self._last_mobility_detections)
        if metrics["people"]:
            from src.vision.person_tracker import draw_person_tracks
            primary_id = next((p["track_id"] for p in metrics["people"] if p["is_primary"]), None)
            draw_person_tracks(frame, self._last_people, primary_track_id=primary_id)

        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (420, 350), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        cv2.putText(frame, f"Fonte: {self.name}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        id_text = self.tracker.registered_id
        if metrics["person_count"] > 1:
            id_text += f" (+{metrics['person_count'] - 1} pessoa(s) no ambiente)"
        cv2.putText(frame, f"ID: {id_text}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame, f"Expressao: {metrics['emotion']}", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(frame, f"Cabeca: {metrics['head_pose']}", (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        cv2.putText(frame, f"Postura: {metrics['posture']}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 255, 100), 2)
        cv2.putText(frame, f"Movimento: {metrics['motion']}", (20, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 100), 2)
        cv2.putText(frame, f"Braco Levantado: {'Sim' if metrics['arm_raised'] else 'Nao'}", (20, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 2)
        cv2.putText(frame, f"Mao no Rosto: {'Sim' if metrics['hand_near_face'] else 'Nao'}", (20, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 2)
        cv2.putText(frame, f"Gestos: {gestures_text}", (20, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 120, 255), 2)
        cv2.putText(frame, f"Dispositivos: {mobility_text}", (20, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)
        cv2.putText(frame, f"FPS: {self.fps:.1f}", (20, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        if metrics["fall_alert"]:
            cv2.rectangle(frame, (0, 0), (w, h), (0, 0, 255), 6)
            cv2.putText(frame, "ALERTA: QUEDA DETECTADA!", (30, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 3)
        elif metrics["drowsy_alert"]:
            cv2.rectangle(frame, (0, 0), (w, h), (0, 140, 255), 6)
            cv2.putText(frame, "ALERTA: SINAL DE SONOLENCIA!", (30, h - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 140, 255), 3)

        cv2.imshow(f"Kinesis SMA-TR - {self.name}", frame)

    def close(self):
        self.cam.stop()
        self.pose_detector.close()
        self.face_detector.close()
        self.gesture_recognizer.close()


def main():
    ensure_models_downloaded()

    app_config = load_app_config()
    contacts = load_contacts()
    notifier = WhatsAppNotifier(app_config.whatsapp)
    dispatcher = NotificationDispatcher(app_config, contacts, notifier)
    dispatcher.redeliver_pending()

    event_logger = EventLogger()
    if app_config.storage.retention_hours > 0:
        event_logger.start_auto_purge(
            retention_hours=app_config.storage.retention_hours,
            check_interval_seconds=app_config.storage.purge_interval_minutes * 60.0,
        )

    checkin_scheduler = CheckinScheduler(app_config, contacts, dispatcher, event_logger=event_logger)
    checkin_scheduler.start()

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

    voice_controllers = create_voice_controllers(app_config, contacts, dispatcher, event_logger)

    remote_server = None
    if app_config.remote_server.enabled:
        remote_server = RemoteStatusServer(
            host=app_config.remote_server.host,
            port=app_config.remote_server.port,
            token=app_config.remote_server.resolve_token(),
            status_provider=lambda: build_status(pipelines, app_config, voice_controllers),
            history_provider=lambda limit: event_logger.read_recent(limit=limit),
            snapshot_fps=app_config.remote_server.snapshot_fps,
        )
        remote_server.start()

    print(f"SMA-TR iniciado com {len(pipelines)} camera(s). Pressione 'q' em qualquer janela para sair.")

    try:
        while True:
            for pipeline in pipelines:
                result = pipeline.process_next_frame()
                if result is None:
                    continue
                frame, metrics = result
                pipeline.render(frame, metrics)

                now = time.time()
                if remote_server is not None and remote_server.should_capture_snapshot(pipeline.name, now=now):
                    remote_server.set_snapshot(pipeline.name, encode_frame_jpeg_bytes(frame))

                for event in metrics["events"]:
                    print(f"[{event.severity}] {event.event_id} ({pipeline.name}): {event.message}")
                    event_logger.log(event)
                    frame_b64 = encode_frame_jpeg_base64(frame)
                    dispatcher.dispatch(event, frame_b64)

            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                break
    finally:
        for pipeline in pipelines:
            pipeline.close()
        for controller in voice_controllers:
            controller.stop()
        checkin_scheduler.stop()
        event_logger.stop_auto_purge()
        if remote_server is not None:
            remote_server.stop()
        notifier.close()
        cv2.destroyAllWindows()


def create_voice_controllers(app_config: AppConfig, contacts: ContactsFile, dispatcher, event_logger):
    """Um VoiceController por câmera, cada um com sua própria captura de
    áudio e seu próprio SpeechTranscriber (modelos de ASR não são seguros
    para chamadas concorrentes vindas de threads diferentes, e cada
    VoiceController roda na sua própria thread — diferente do
    MobilityAidDetector, chamado sempre sequencialmente pelo laço de
    vídeo). Reaproveitado por main.py e gui_main.py."""
    if not app_config.voice.enabled:
        return []

    from src.audio.transcriber import SpeechTranscriber
    from src.audio.voice_controller import VoiceController

    controllers = []
    for cam in app_config.cameras:
        transcriber = SpeechTranscriber(
            model_size=app_config.voice.model_size, language=app_config.voice.language,
            model_dir=app_config.voice.resolve_model_dir(),
        )
        controller = VoiceController(
            source_name=cam.name, audio_source=cam.resolve_src(), contacts=contacts,
            dispatcher=dispatcher, event_logger=event_logger, transcriber=transcriber,
            language=app_config.voice.language, chunk_seconds=app_config.voice.chunk_seconds,
        ).start()
        controllers.append(controller)
    return controllers


if __name__ == "__main__":
    main()
