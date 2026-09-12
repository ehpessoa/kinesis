"""Pipeline multi-fonte: webcam local, câmera IP na rede Wi-Fi local e câmera IP
remota acessada via sub-rede Tailscale (5G) — ver README para o guia de setup
do hardware. As três fontes rodam concorrentemente, cada uma com sua própria
thread de captura, seus próprios detectores MediaPipe e seu próprio
BehaviorTracker, para não misturar estado/tracking entre streams independentes.
"""
import time

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

from capture import ThreadedCamera
from config import load_camera_sources
from tracking import (
    BehaviorTracker,
    FACE_CONNECTIONS,
    FACE_MODEL_PATH,
    GESTURE_MODEL_PATH,
    HAND_CONNECTIONS,
    POSE_CONNECTIONS,
    POSE_MODEL_PATH,
    draw_landmarks,
    ensure_models_downloaded,
)


class CameraPipeline:
    """Agrupa tudo que é específico de UMA fonte de vídeo: captura, detectores
    MediaPipe (com sua própria linha de tempo) e o tracker comportamental."""

    def __init__(self, name: str, src):
        self.name = name
        self.cam = ThreadedCamera(src, name=name).start()
        self.tracker = BehaviorTracker()
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
        frame novo desde a última chamada (evita timestamp duplicado no
        MediaPipe, que exige timestamps estritamente crescentes)."""
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
        # que a câmera fique momentaneamente sem sinal e reconecte.
        timestamp_ms = frame_count

        pose_results = self.pose_detector.detect_for_video(mp_image, timestamp_ms)
        face_results = self.face_detector.detect_for_video(mp_image, timestamp_ms)
        gesture_results = self.gesture_recognizer.recognize_for_video(mp_image, timestamp_ms)

        metrics = {
            "emotion": "N/A", "head_pose": "N/A", "posture": "N/A", "motion": "N/A",
            "arm_raised": False, "hand_near_face": False, "drowsy_alert": False,
            "fall_alert": False, "gestures": {},
        }

        if face_results.face_landmarks:
            for face_landmarks in face_results.face_landmarks:
                metrics["head_pose"] = self.tracker.estimate_head_pose(face_landmarks, w, h)
                draw_landmarks(frame, face_landmarks, FACE_CONNECTIONS, w, h, (0, 255, 255), radius=1)
            if face_results.face_blendshapes:
                blend_data = self.tracker.analyze_blendshapes(face_results.face_blendshapes[0])
                metrics["emotion"] = blend_data["emotion"]
                metrics["drowsy_alert"] = blend_data["drowsy_alert"]

        if pose_results.pose_landmarks:
            for pose_landmarks in pose_results.pose_landmarks:
                draw_landmarks(frame, pose_landmarks, POSE_CONNECTIONS, w, h, (100, 255, 100), radius=3)
                pose_data = self.tracker.analyze_pose(pose_landmarks, w, h)
                metrics["posture"] = pose_data["posture"]
                metrics["motion"] = pose_data["dynamic_state"]
                metrics["fall_alert"] = pose_data["fall_alert"]
                metrics["arm_raised"] = pose_data["arm_raised"]
                metrics["hand_near_face"] = pose_data["hand_near_face"]

        if gesture_results.hand_landmarks:
            for hand_landmarks in gesture_results.hand_landmarks:
                draw_landmarks(frame, hand_landmarks, HAND_CONNECTIONS, w, h, (255, 120, 255), radius=2)
            metrics["gestures"] = self.tracker.analyze_gestures(gesture_results)

        curr_frame_time = time.time()
        self.fps = 1.0 / max(1e-5, (curr_frame_time - self.prev_frame_time))
        self.prev_frame_time = curr_frame_time

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

        cv2.imshow(f"Kinesis - {self.name}", frame)

    def close(self):
        self.cam.stop()
        self.pose_detector.close()
        self.face_detector.close()
        self.gesture_recognizer.close()


def main():
    ensure_models_downloaded()

    sources = load_camera_sources()
    pipelines = [CameraPipeline(name=src.name, src=src.src) for src in sources]

    print(f"Pipeline Multi-Fonte iniciado com {len(pipelines)} camera(s). Pressione 'q' em qualquer janela para sair.")

    try:
        while True:
            for pipeline in pipelines:
                result = pipeline.process_next_frame()
                if result is None:
                    continue
                frame, metrics = result
                pipeline.render(frame, metrics)

            if cv2.waitKey(1) & 0xFF in (ord('q'), 27):
                break
    finally:
        for pipeline in pipelines:
            pipeline.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
