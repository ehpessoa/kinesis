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
from src.notifications.whatsapp_client import WhatsAppNotifier, encode_frame_jpeg_base64
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


class NotificationDispatcher:
    """Encaminha EventNotification para os contatos configurados via WhatsApp,
    respeitando a matriz de eventos (habilitado/desabilitado, destinatários)."""

    def __init__(self, config: AppConfig, contacts: ContactsFile, notifier: WhatsAppNotifier):
        self.config = config
        self.contacts = contacts
        self.notifier = notifier

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
            threading.Thread(
                target=self.notifier.send_event,
                args=(event, contact.whatsapp_number, frame_b64),
                daemon=True,
            ).start()


class CameraPipeline:
    """Tudo que é específico de UMA fonte de vídeo: captura, detectores
    MediaPipe (com timeline própria), BehaviorTracker e EventEngine."""

    def __init__(self, name: str, src):
        self.name = name
        self.cam = ThreadedCamera(src, name=name).start()
        self.tracker = BehaviorTracker()
        self.event_engine = EventEngine(source_name=name)
        self.last_frame_count = -1
        self.prev_frame_time = time.time()
        self.fps = 0.0

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

        pose_results = self.pose_detector.detect_for_video(mp_image, timestamp_ms)
        face_results = self.face_detector.detect_for_video(mp_image, timestamp_ms)
        gesture_results = self.gesture_recognizer.recognize_for_video(mp_image, timestamp_ms)

        pose_data = {
            "posture": "N/A", "dynamic_state": "Estatico", "fall_alert": False,
            "arm_raised": False, "hand_near_face": False, "torso_h": 0.0,
        }
        blend_data = {"emotion": "N/A", "drowsy_alert": False, "distress_score": 0.0}
        head_pose = "N/A"
        gestures = {}

        if face_results.face_landmarks:
            for face_landmarks in face_results.face_landmarks:
                head_pose = self.tracker.estimate_head_pose(face_landmarks, w, h)
                draw_landmarks(frame, face_landmarks, FACE_CONNECTIONS, w, h, (0, 255, 255), radius=1)
            if face_results.face_blendshapes:
                blend_data = self.tracker.analyze_blendshapes(face_results.face_blendshapes[0])

        if pose_results.pose_landmarks:
            for pose_landmarks in pose_results.pose_landmarks:
                draw_landmarks(frame, pose_landmarks, POSE_CONNECTIONS, w, h, (100, 255, 100), radius=3)
                pose_data = self.tracker.analyze_pose(pose_landmarks, w, h)

        if gesture_results.hand_landmarks:
            for hand_landmarks in gesture_results.hand_landmarks:
                draw_landmarks(frame, hand_landmarks, HAND_CONNECTIONS, w, h, (255, 120, 255), radius=2)
            gestures = self.tracker.analyze_gestures(gesture_results)

        events = self.event_engine.update(pose_data, blend_data, now=time.time())

        curr_frame_time = time.time()
        self.fps = 1.0 / max(1e-5, (curr_frame_time - self.prev_frame_time))
        self.prev_frame_time = curr_frame_time

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
        }
        return frame, metrics

    def render(self, frame, metrics):
        h, w, _ = frame.shape
        gestures_text = ", ".join(f"{side}: {label}" for side, label in metrics["gestures"].items()) or "Nenhum"

        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (420, 320), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        cv2.putText(frame, f"Fonte: {self.name}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        cv2.putText(frame, f"ID: {self.tracker.registered_id}", (20, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(frame, f"Expressao: {metrics['emotion']}", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(frame, f"Cabeca: {metrics['head_pose']}", (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        cv2.putText(frame, f"Postura: {metrics['posture']}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 255, 100), 2)
        cv2.putText(frame, f"Movimento: {metrics['motion']}", (20, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 200, 100), 2)
        cv2.putText(frame, f"Braco Levantado: {'Sim' if metrics['arm_raised'] else 'Nao'}", (20, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 2)
        cv2.putText(frame, f"Mao no Rosto: {'Sim' if metrics['hand_near_face'] else 'Nao'}", (20, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 255, 200), 2)
        cv2.putText(frame, f"Gestos: {gestures_text}", (20, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 120, 255), 2)
        cv2.putText(frame, f"FPS: {self.fps:.1f}", (20, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

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

    pipelines = [CameraPipeline(name=cam.name, src=cam.resolve_src()) for cam in app_config.cameras]

    print(f"SMA-TR iniciado com {len(pipelines)} camera(s). Pressione 'q' em qualquer janela para sair.")

    try:
        while True:
            for pipeline in pipelines:
                result = pipeline.process_next_frame()
                if result is None:
                    continue
                frame, metrics = result
                pipeline.render(frame, metrics)

                for event in metrics["events"]:
                    print(f"[{event.severity}] {event.event_id} ({pipeline.name}): {event.message}")
                    frame_b64 = encode_frame_jpeg_base64(frame)
                    dispatcher.dispatch(event, frame_b64)

            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                break
    finally:
        for pipeline in pipelines:
            pipeline.close()
        notifier.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
