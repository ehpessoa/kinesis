"""Download de modelos MediaPipe, constantes de conexões de landmarks e desenho
de esqueleto/rosto/mãos sobre o frame."""
import os
import urllib.request

import cv2

from mediapipe.tasks.python.vision import (
    FaceLandmarksConnections,
    HandLandmarksConnections,
    PoseLandmarksConnections,
)

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models")
MODELS_DIR = os.path.normpath(MODELS_DIR)
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
