"""Testes de src/vision/object_detector.py.

O modelo real (YOLO-World + encoder MobileCLIP) soma ~600MB de pesos na
primeira execução — pesado demais para forçar em qualquer `pytest` local
ou de CI. Os testes que carregam o modelo de verdade são pulados
automaticamente se os pesos ainda não estiverem cacheados em models/
(mesmo caminho que a aplicação usa); rode a aplicação uma vez com
object_detection.enabled=true para cachear e habilitar esses testes.
"""
import os

import numpy as np
import pytest

from src.vision.object_detector import MOBILITY_AID_PROMPTS, MobilityAidDetector, draw_object_detections
from src.vision.weights import MODELS_DIR

_WEIGHTS_CACHED = (
    os.path.exists(os.path.join(MODELS_DIR, "yolov8s-world.pt"))
    and os.path.exists(os.path.join(MODELS_DIR, "mobileclip_blt.ts"))
)

requires_cached_weights = pytest.mark.skipif(
    not _WEIGHTS_CACHED,
    reason="pesos do YOLO-World/MobileCLIP (~600MB) nao cacheados em models/ - rode a app uma vez com "
           "object_detection.enabled=true para habilitar este teste",
)


def test_mobility_aid_prompts_cover_the_three_target_labels():
    assert set(MOBILITY_AID_PROMPTS.keys()) == {"Bengala", "Andador", "Cadeira de Rodas"}
    assert all(len(synonyms) >= 1 for synonyms in MOBILITY_AID_PROMPTS.values())


def test_draw_object_detections_does_not_raise():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    detections = [{"label": "Bengala", "confidence": 0.5, "bbox": (10, 10, 50, 100)}]
    draw_object_detections(frame, detections)  # nao deve lancar excecao


@requires_cached_weights
def test_mobility_aid_detector_runs_on_synthetic_frame():
    detector = MobilityAidDetector(confidence=0.35)
    frame = (np.random.rand(240, 320, 3) * 255).astype(np.uint8)

    detections = detector.detect(frame)

    assert isinstance(detections, list)
    for det in detections:
        assert det["label"] in MOBILITY_AID_PROMPTS
        assert 0.0 <= det["confidence"] <= 1.0
        assert len(det["bbox"]) == 4
