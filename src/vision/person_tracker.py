"""Rastreamento contínuo de pessoas via ByteTrack — mitigação do item 4.1.3
do plano (seção "Requisitos Não Viáveis"): reidentificação biométrica facial
constante é pouco confiável em câmeras distantes ou em ângulos inclinados.
Em vez disso, a pessoa recebe um ID de rastreamento no momento em que entra
no ambiente, mantido por continuidade de movimento/aparência (ByteTrack)
enquanto ela permanece visível — sem depender de reconhecimento facial.

Usa YOLOv8 padrão (classe "person" do COCO-80, presente nos pesos
pré-treinados normais) com o tracker ByteTrack embutido no ultralytics —
diferente de src/vision/object_detector.py, que precisou de YOLO-World
porque bengala/andador/cadeira de rodas não são classes COCO.
"""
from ultralytics import YOLO

from src.vision.weights import download_into_models_dir

PERSON_MODEL_WEIGHTS = "yolov8n.pt"
PERSON_CLASS_ID = 0  # "person" no COCO-80


class PersonTracker:
    """Rastreador de UMA fonte de câmera.

    Assim como os detectores MediaPipe em modo VIDEO (ver
    src/vision/detectors.py), o ByteTrack embutido no ultralytics associa
    detecções entre frames usando o histórico de tracks mantido na própria
    instância do modelo (`persist=True`). Compartilhar uma única instância
    entre câmeras misturaria tracks de cenas físicas independentes — cada
    CameraPipeline precisa da sua própria instância de PersonTracker.
    """

    def __init__(self, confidence: float = 0.4):
        self.confidence = confidence
        with download_into_models_dir():
            self.model = YOLO(PERSON_MODEL_WEIGHTS)
        self._primary_track_id = None

    def track(self, frame) -> list:
        results = self.model.track(
            frame, persist=True, tracker="bytetrack.yaml",
            classes=[PERSON_CLASS_ID], conf=self.confidence, verbose=False,
        )[0]

        people = []
        boxes = results.boxes
        if boxes is not None and boxes.id is not None:
            for box, track_id in zip(boxes, boxes.id.tolist()):
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                people.append({
                    "track_id": int(track_id),
                    "bbox": (x1, y1, x2, y2),
                    "confidence": float(box.conf.item()),
                    "area": (x2 - x1) * (y2 - y1),
                })
        return people

    def pick_primary(self, people: list):
        """Escolhe a pessoa "principal" a acompanhar com o pipeline de
        pose/rosto/gestos (que hoje analisa 1 pessoa por câmera).

        Preferimos manter o mesmo track_id do frame anterior — é
        exatamente a continuidade que substitui a reidentificação facial —
        e só trocamos de pessoa principal quando o track anterior some do
        campo de visão."""
        if not people:
            self._primary_track_id = None
            return None

        if self._primary_track_id is not None:
            for person in people:
                if person["track_id"] == self._primary_track_id:
                    return person

        primary = max(people, key=lambda p: p["area"])
        self._primary_track_id = primary["track_id"]
        return primary


def draw_person_tracks(frame, people, primary_track_id=None, color=(50, 220, 50), primary_color=(255, 255, 0)):
    import cv2

    for person in people:
        is_primary = person["track_id"] == primary_track_id
        box_color = primary_color if is_primary else color
        x1, y1, x2, y2 = person["bbox"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
        label = f"Pessoa {person['track_id']}" + (" (principal)" if is_primary else "")
        cv2.putText(frame, label, (x1, max(15, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)
