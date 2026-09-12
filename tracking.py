"""Download de modelos, desenho de landmarks e análise comportamental/cinemática.

BehaviorTracker mantém estado entre frames (histórico de quadril, velocidade dos
punhos, tempo de olhos fechados). Por isso cada fonte de câmera precisa da sua
própria instância: compartilhar um tracker entre câmeras misturaria o estado de
pessoas/cenas diferentes.
"""
import os
import time
import urllib.request
from collections import deque

import cv2
import numpy as np

from mediapipe.tasks.python.vision import (
    FaceLandmarksConnections,
    HandLandmarksConnections,
    PoseLandmarksConnections,
)

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
POSE_MODEL_PATH = os.path.join(MODELS_DIR, "pose_landmarker_lite.task")
FACE_MODEL_PATH = os.path.join(MODELS_DIR, "face_landmarker.task")
GESTURE_MODEL_PATH = os.path.join(MODELS_DIR, "gesture_recognizer.task")

MODEL_URLS = {
    POSE_MODEL_PATH: "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    FACE_MODEL_PATH: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    GESTURE_MODEL_PATH: "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task",
}


def ensure_models_downloaded():
    os.makedirs(MODELS_DIR, exist_ok=True)
    for path, url in MODEL_URLS.items():
        if not os.path.exists(path):
            print(f"Baixando modelo: {os.path.basename(path)}...")
            urllib.request.urlretrieve(url, path)


POSE_CONNECTIONS = [(c.start, c.end) for c in PoseLandmarksConnections.POSE_LANDMARKS]
FACE_CONNECTIONS = [
    (c.start, c.end)
    for c in (
        FaceLandmarksConnections.FACE_LANDMARKS_FACE_OVAL
        + FaceLandmarksConnections.FACE_LANDMARKS_LEFT_EYE
        + FaceLandmarksConnections.FACE_LANDMARKS_LEFT_EYEBROW
        + FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_EYE
        + FaceLandmarksConnections.FACE_LANDMARKS_RIGHT_EYEBROW
        + FaceLandmarksConnections.FACE_LANDMARKS_LIPS
    )
]
HAND_CONNECTIONS = [(c.start, c.end) for c in HandLandmarksConnections.HAND_CONNECTIONS]

GESTURE_LABELS = {
    "Closed_Fist": "Punho Fechado",
    "Open_Palm": "Palma Aberta",
    "Pointing_Up": "Apontando p/ Cima",
    "Thumb_Down": "Joinha Negativo",
    "Thumb_Up": "Joinha Positivo",
    "Victory": "Sinal de Vitoria",
    "ILoveYou": "Eu Te Amo",
}


def draw_landmarks(frame, landmarks, connections, frame_w, frame_h, color, radius=2):
    points = [(int(lm.x * frame_w), int(lm.y * frame_h)) for lm in landmarks]
    for start, end in connections:
        cv2.line(frame, points[start], points[end], color, 1)
    for point in points:
        cv2.circle(frame, point, radius, color, -1)


class BehaviorTracker:
    """Gerencia a máquina de estados, cinemática corporal e expressões faciais
    de UMA única fonte de vídeo. Instancie uma por câmera/pessoa monitorada."""

    def __init__(self, history_len: int = 15):
        self.history_len = history_len
        self.hip_y_history = deque(maxlen=history_len)
        self.wrist_speed_history = deque(maxlen=history_len)
        self.prev_wrists = None
        self.fall_state = False
        self.fall_timestamp = 0
        self.eyes_closed_since = None
        self.registered_id = "Usuario_Principal"  # Mock de Biometria para o Piloto

    def analyze_blendshapes(self, blendshapes) -> dict:
        scores = {c.category_name: c.score for c in blendshapes}

        smile = (scores.get("mouthSmileLeft", 0) + scores.get("mouthSmileRight", 0)) / 2
        frown = (scores.get("mouthFrownLeft", 0) + scores.get("mouthFrownRight", 0)) / 2
        brow_down = (scores.get("browDownLeft", 0) + scores.get("browDownRight", 0)) / 2
        brow_up = scores.get("browInnerUp", 0)
        jaw_open = scores.get("jawOpen", 0)
        eye_wide = (scores.get("eyeWideLeft", 0) + scores.get("eyeWideRight", 0)) / 2
        nose_sneer = (scores.get("noseSneerLeft", 0) + scores.get("noseSneerRight", 0)) / 2
        mouth_press = (scores.get("mouthPressLeft", 0) + scores.get("mouthPressRight", 0)) / 2
        eye_blink = (scores.get("eyeBlinkLeft", 0) + scores.get("eyeBlinkRight", 0)) / 2

        if nose_sneer > 0.4:
            emotion = "Nojo / Desagrado"
        elif jaw_open > 0.5 and (brow_up > 0.3 or eye_wide > 0.3):
            emotion = "Surpreso"
        elif smile > 0.4:
            emotion = "Alegria / Sorriso"
        elif frown > 0.3 and brow_up > 0.25:
            emotion = "Triste"
        elif brow_down > 0.4 and mouth_press > 0.2:
            emotion = "Raiva / Tensao"
        else:
            emotion = "Neutro"

        drowsy_alert = False
        now = time.time()
        if eye_blink > 0.55:
            if self.eyes_closed_since is None:
                self.eyes_closed_since = now
            elif now - self.eyes_closed_since > 1.2:
                drowsy_alert = True
        else:
            self.eyes_closed_since = None

        return {"emotion": emotion, "drowsy_alert": drowsy_alert}

    def estimate_head_pose(self, face_landmarks, frame_w, frame_h) -> str:
        nose = face_landmarks[1]
        forehead = face_landmarks[10]
        chin = face_landmarks[152]
        left_edge = face_landmarks[234]
        right_edge = face_landmarks[454]

        face_span_x = max(1.0, (right_edge.x - left_edge.x) * frame_w)
        horizontal_ratio = ((nose.x * frame_w) - (left_edge.x * frame_w)) / face_span_x

        face_span_y = max(1.0, (chin.y - forehead.y) * frame_h)
        vertical_ratio = ((nose.y * frame_h) - (forehead.y * frame_h)) / face_span_y

        if horizontal_ratio < 0.35:
            return "Direita"
        elif horizontal_ratio > 0.65:
            return "Esquerda"
        elif vertical_ratio > 0.62:
            return "Baixo"
        elif vertical_ratio < 0.45:
            return "Cima"
        else:
            return "Frente"

    def analyze_pose(self, pose_landmarks, frame_w, frame_h) -> dict:
        lms = pose_landmarks

        nose = np.array([lms[0].x * frame_w, lms[0].y * frame_h])
        l_sh = np.array([lms[11].x * frame_w, lms[11].y * frame_h])
        r_sh = np.array([lms[12].x * frame_w, lms[12].y * frame_h])
        l_hip = np.array([lms[23].x * frame_w, lms[23].y * frame_h])
        r_hip = np.array([lms[24].x * frame_w, lms[24].y * frame_h])
        l_knee = np.array([lms[25].x * frame_w, lms[25].y * frame_h])
        r_knee = np.array([lms[26].x * frame_w, lms[26].y * frame_h])
        l_wrist = np.array([lms[15].x * frame_w, lms[15].y * frame_h])
        r_wrist = np.array([lms[16].x * frame_w, lms[16].y * frame_h])

        shoulder_center = (l_sh + r_sh) / 2.0
        hip_center = (l_hip + r_hip) / 2.0
        knee_center = (l_knee + r_knee) / 2.0

        torso_height = np.linalg.norm(shoulder_center - hip_center)
        body_width = np.linalg.norm(l_sh - r_sh)

        dx = abs(shoulder_center[0] - hip_center[0])
        dy = max(1.0, abs(shoulder_center[1] - hip_center[1]))
        trunk_angle = np.degrees(np.arctan(dx / dy))

        posture = "Em pe"
        if trunk_angle > 50.0 or dy < (body_width * 0.6):
            posture = "Deitado"
        else:
            hip_knee_dy = knee_center[1] - hip_center[1]
            if hip_knee_dy < (torso_height * 0.7):
                posture = "Sentado"

        current_wrists = (l_wrist + r_wrist) / 2.0
        if self.prev_wrists is not None:
            w_speed = np.linalg.norm(current_wrists - self.prev_wrists)
            self.wrist_speed_history.append(w_speed)
        self.prev_wrists = current_wrists

        avg_wrist_motion = np.mean(self.wrist_speed_history) if self.wrist_speed_history else 0

        dynamic_state = "Estatico"
        if avg_wrist_motion > 15.0:
            dynamic_state = "Inquieto / Mexendo"
        elif avg_wrist_motion > 4.0:
            dynamic_state = "Ativo / Em Movimento"

        arm_raised = bool(
            (l_wrist[1] < shoulder_center[1] - torso_height * 0.2)
            or (r_wrist[1] < shoulder_center[1] - torso_height * 0.2)
        )
        hand_near_face = bool(
            (np.linalg.norm(l_wrist - nose) < body_width * 0.4)
            or (np.linalg.norm(r_wrist - nose) < body_width * 0.4)
        )

        self.hip_y_history.append(hip_center[1])
        fall_alert = False

        if len(self.hip_y_history) == self.history_len:
            dy_descent = self.hip_y_history[-1] - self.hip_y_history[0]
            if dy_descent > (torso_height * 0.8) and posture == "Deitado":
                self.fall_state = True
                self.fall_timestamp = time.time()

        if self.fall_state:
            if time.time() - self.fall_timestamp < 3.0:
                fall_alert = True
            else:
                self.fall_state = False

        return {
            "posture": posture,
            "dynamic_state": dynamic_state,
            "fall_alert": fall_alert,
            "arm_raised": arm_raised,
            "hand_near_face": hand_near_face,
            "torso_h": torso_height,
        }

    def analyze_gestures(self, gesture_results) -> dict:
        hands = {}
        for i in range(len(gesture_results.hand_landmarks)):
            handedness = gesture_results.handedness[i][0].category_name if gesture_results.handedness[i] else None
            gesture_cat = gesture_results.gestures[i][0] if gesture_results.gestures[i] else None
            side = "Esquerda" if handedness == "Left" else "Direita"
            label = GESTURE_LABELS.get(gesture_cat.category_name) if gesture_cat else None
            if label:
                hands[side] = label
        return hands
