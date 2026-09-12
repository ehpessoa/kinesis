"""Detector de dispositivos de mobilidade assistiva (bengala, andador,
cadeira de rodas) via YOLO-World (detecção de vocabulário aberto).

Nenhuma dessas três classes existe no conjunto fixo COCO-80 dos pesos YOLOv8
padrão (person, chair, couch, ... — sem "cane", "walker" ou "wheelchair").
Em vez de coletar e treinar um dataset customizado, usamos YOLO-World: ele
casa cada caixa candidata contra embeddings de texto (CLIP) das classes que
definimos abaixo, permitindo detectar categorias arbitrárias por descrição
textual em vez de um classificador fechado.

O encoder de texto padrão do YOLO-World (CLIP da OpenAI, hospedado em
openaipublic.azureedge.net) é bloqueado pela política de rede deste
ambiente. Usamos o MobileCLIP (Apple), servido pelos releases do GitHub da
Ultralytics, para calcular os embeddings das classes — uma única vez, no
carregamento do modelo. A partir daí, a detecção por frame usa somente o
backbone YOLO (rápido); o MobileCLIP não entra no laço de vídeo.
"""
import contextlib
import os

import cv2
from ultralytics import YOLO

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "models")
MODELS_DIR = os.path.normpath(MODELS_DIR)
YOLO_WORLD_WEIGHTS = "yolov8s-world.pt"

# rótulo em português -> sinônimos em inglês (CLIP/MobileCLIP funcionam
# melhor com prompts em inglês; múltiplos sinônimos por rótulo aumentam a
# chance de detecção sem exigir um dataset rotulado).
MOBILITY_AID_PROMPTS = {
    "Bengala": ["walking cane", "walking stick"],
    "Andador": ["walker", "rollator", "walking frame"],
    "Cadeira de Rodas": ["wheelchair"],
}


def _flatten_prompts():
    prompts, labels = [], []
    for label, synonyms in MOBILITY_AID_PROMPTS.items():
        for synonym in synonyms:
            prompts.append(synonym)
            labels.append(label)
    return prompts, labels


def _use_mobileclip_text_backend():
    """Faz o YOLO-World usar o encoder de texto MobileCLIP (via GitHub) em
    vez do CLIP padrão da OpenAI (host bloqueado pela política de rede).
    Afeta só o cálculo dos embeddings de classe, feito uma única vez."""
    import ultralytics.nn.text_model as text_model_module

    if getattr(text_model_module, "_kinesis_patched", False):
        return
    original_build = text_model_module.build_text_model

    def patched(variant, device=None):
        return original_build("mobileclip:s0", device=device)

    text_model_module.build_text_model = patched
    text_model_module._kinesis_patched = True


@contextlib.contextmanager
def _download_into_models_dir():
    """Os pesos do YOLO-World/MobileCLIP são baixados relativos ao
    diretório de trabalho atual; entramos temporariamente em models/ (já
    gitignorada, mesma pasta dos modelos MediaPipe) para não poluir a raiz
    do repositório."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    prev_cwd = os.getcwd()
    os.chdir(MODELS_DIR)
    try:
        yield
    finally:
        os.chdir(prev_cwd)


class MobilityAidDetector:
    """Detector compartilhável entre todas as fontes de câmera.

    Ao contrário dos detectores MediaPipe em modo VIDEO (que têm estado
    temporal e por isso precisam de uma instância por fonte — ver
    src/vision/detectors.py), a detecção YOLO é sem estado entre frames: uma
    única instância pode atender todas as CameraPipeline sem risco de
    misturar contexto entre câmeras.
    """

    def __init__(self, confidence: float = 0.35):
        self.confidence = confidence
        _use_mobileclip_text_backend()
        prompts, labels = _flatten_prompts()
        self._class_labels = labels  # índice de classe do modelo -> rótulo em português

        with _download_into_models_dir():
            self.model = YOLO(YOLO_WORLD_WEIGHTS)
            self.model.set_classes(prompts)

    def detect(self, frame) -> list:
        results = self.model.predict(frame, conf=self.confidence, verbose=False)[0]
        detections = []
        for box in results.boxes:
            class_idx = int(box.cls.item())
            label = self._class_labels[class_idx] if class_idx < len(self._class_labels) else "Desconhecido"
            x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
            detections.append({
                "label": label,
                "confidence": float(box.conf.item()),
                "bbox": (x1, y1, x2, y2),
            })
        return detections


def draw_object_detections(frame, detections, color=(0, 200, 255)):
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f"{det['label']} {det['confidence']:.0%}"
        cv2.putText(frame, text, (x1, max(15, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
