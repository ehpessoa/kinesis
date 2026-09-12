"""Integração de main.CameraPipeline: captura mockada (sem hardware de
câmera), detectores MediaPipe reais rodando sobre frames sintéticos (sem
pessoas — landmarks vazios é o caminho normal, não um erro) e o rastreador
de pessoa com resultados de YOLO mockados (ver test_person_tracker.py)."""
import numpy as np
import pytest
import torch

import main as main_mod


class FakeCam:
    def __init__(self, *a, **kw):
        self.src = 0
        self._count = 0
        self.grabbed = True

    def start(self):
        return self

    def is_local_webcam(self):
        return True

    def read(self):
        self._count += 1
        frame = (np.random.rand(120, 160, 3) * 255).astype("uint8")
        return True, frame, self._count

    def stop(self):
        pass


@pytest.fixture(autouse=True)
def fake_camera(monkeypatch):
    monkeypatch.setattr(main_mod, "ThreadedCamera", FakeCam)


def test_pipeline_without_person_tracking_preserves_previous_behavior():
    pipeline = main_mod.CameraPipeline(name="Sem Rastreamento", src=0, person_tracking_enabled=False)
    try:
        result = pipeline.process_next_frame()
        assert result is not None
        _, metrics = result
        assert metrics["person_count"] == 0
        assert pipeline.tracker.registered_id == "N/A"
        assert metrics["mobility_aids"] == []
        assert metrics["events"] == []
    finally:
        pipeline.close()


def test_pipeline_returns_none_without_new_frame():
    pipeline = main_mod.CameraPipeline(name="Teste", src=0, person_tracking_enabled=False)
    try:
        pipeline.process_next_frame()
        # Simula a camera "travada" sem produzir frame novo: read() volta a
        # entregar o mesmo frame_count da ultima chamada.
        pipeline.cam._count -= 1
        assert pipeline.process_next_frame() is None
    finally:
        pipeline.close()


class FakeBox:
    def __init__(self, xyxy, conf):
        self.xyxy = torch.tensor([xyxy], dtype=torch.float32)
        self.conf = torch.tensor([conf], dtype=torch.float32)


class FakeBoxes:
    def __init__(self, entries):
        self._boxes = [FakeBox(xyxy, conf) for xyxy, conf, _id in entries]
        self.id = torch.tensor([_id for _, _, _id in entries], dtype=torch.float32) if entries else None

    def __iter__(self):
        return iter(self._boxes)


class FakeResult:
    def __init__(self, boxes):
        self.boxes = boxes


def test_pipeline_with_person_tracking_updates_registered_id():
    pipeline = main_mod.CameraPipeline(name="Com Rastreamento", src=0, person_tracking_enabled=True)
    try:
        pipeline.person_tracker.model.track = lambda *a, **kw: [FakeResult(FakeBoxes([((10, 10, 100, 200), 0.9, 42)]))]

        result = pipeline.process_next_frame()
        assert result is not None
        _, metrics = result
        assert metrics["person_count"] == 1
        assert pipeline.tracker.registered_id == "Pessoa 42"
    finally:
        pipeline.close()


def test_pipeline_person_label_reaches_event_notification():
    pipeline = main_mod.CameraPipeline(name="Com Rastreamento", src=0, person_tracking_enabled=True)
    try:
        pipeline.person_tracker.model.track = lambda *a, **kw: [FakeResult(FakeBoxes([((10, 10, 100, 200), 0.9, 42)]))]
        pipeline.process_next_frame()

        pipeline.event_engine._prev_posture = "Em pe"
        events = pipeline.event_engine.update(
            {"posture": "Sentado", "dynamic_state": "Estatico", "fall_alert": False,
             "arm_raised": False, "hand_near_face": False},
            {"emotion": "Neutro", "drowsy_alert": False, "distress_score": 0.0},
            person_label=pipeline.tracker.registered_id,
        )
        assert events[0].person_label == "Pessoa 42"
    finally:
        pipeline.close()


def test_pipeline_rate_limiting_reduces_detector_calls(monkeypatch):
    fake_clock = {"t": 1000.0}
    monkeypatch.setattr(main_mod.time, "time", lambda: fake_clock["t"])

    pipeline = main_mod.CameraPipeline(
        name="RateTest", src=0, person_tracking_enabled=False,
        pose_fps=5.0, face_fps=2.0, gesture_fps=2.0,
    )
    try:
        pose_calls = []
        orig_pose = pipeline.pose_detector.detect_for_video
        monkeypatch.setattr(pipeline.pose_detector, "detect_for_video",
                             lambda *a, **kw: (pose_calls.append(1), orig_pose(*a, **kw))[1])

        for _ in range(90):  # 3s simulados a 30 fps
            fake_clock["t"] += 1.0 / 30.0
            assert pipeline.process_next_frame() is not None

        # alvo: 5 fps * 3s = ~15 chamadas, com tolerancia
        assert 13 <= len(pose_calls) <= 17
    finally:
        pipeline.close()


def test_pipeline_render_does_not_raise(monkeypatch):
    calls = []
    monkeypatch.setattr(main_mod.cv2, "imshow", lambda name, frame: calls.append(name))

    pipeline = main_mod.CameraPipeline(name="Render Teste", src=0, person_tracking_enabled=False)
    try:
        frame, metrics = pipeline.process_next_frame()
        pipeline.render(frame, metrics)
        assert calls == ["Kinesis SMA-TR - Render Teste"]
    finally:
        pipeline.close()
