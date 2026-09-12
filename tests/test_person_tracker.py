"""Testa a lógica de PersonTracker.pick_primary/track sem depender de
pessoas reais em câmera: os resultados do YOLO são substituídos por dublês
(FakeBox/FakeBoxes) que imitam a API real (`ultralytics.engine.results`).

Carrega o modelo real (yolov8n.pt, ~6MB, cacheado em models/ após a
primeira execução) — leve o suficiente para não precisar de um marcador de
skip como object_detector (YOLO-World + MobileCLIP, ~600MB).
"""
import torch

from src.vision.person_tracker import PersonTracker, draw_person_tracks


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


def fake_track(entries):
    return lambda *a, **kw: [FakeResult(FakeBoxes(entries))]


def make_frame():
    import numpy as np
    return np.zeros((480, 640, 3), dtype=np.uint8)


def test_no_detections_returns_empty_and_no_primary():
    tracker = PersonTracker(confidence=0.4)
    tracker.model.track = fake_track([])

    people = tracker.track(make_frame())
    assert people == []
    assert tracker.pick_primary(people) is None


def test_single_person_becomes_primary():
    tracker = PersonTracker(confidence=0.4)
    tracker.model.track = fake_track([((10, 10, 100, 200), 0.9, 5)])

    people = tracker.track(make_frame())
    assert len(people) == 1 and people[0]["track_id"] == 5

    primary = tracker.pick_primary(people)
    assert primary["track_id"] == 5


def test_primary_track_is_stable_against_larger_newcomer():
    tracker = PersonTracker(confidence=0.4)
    tracker.model.track = fake_track([((10, 10, 100, 200), 0.9, 5)])
    people = tracker.track(make_frame())
    tracker.pick_primary(people)

    # Uma segunda pessoa (id=9), com caixa MAIOR, aparece - a principal nao deve trocar.
    tracker.model.track = fake_track([
        ((10, 10, 100, 200), 0.9, 5),
        ((200, 10, 500, 400), 0.95, 9),
    ])
    people = tracker.track(make_frame())
    primary = tracker.pick_primary(people)
    assert primary["track_id"] == 5


def test_primary_switches_when_previous_track_disappears():
    tracker = PersonTracker(confidence=0.4)
    tracker.model.track = fake_track([
        ((10, 10, 100, 200), 0.9, 5),
        ((200, 10, 500, 400), 0.95, 9),
    ])
    people = tracker.track(make_frame())
    tracker.pick_primary(people)

    tracker.model.track = fake_track([((200, 10, 500, 400), 0.95, 9)])
    people = tracker.track(make_frame())
    primary = tracker.pick_primary(people)
    assert primary["track_id"] == 9


def test_draw_person_tracks_does_not_raise():
    tracker = PersonTracker(confidence=0.4)
    tracker.model.track = fake_track([((10, 10, 100, 200), 0.9, 5)])
    people = tracker.track(make_frame())

    frame = make_frame()
    draw_person_tracks(frame, people, primary_track_id=5)  # nao deve lancar excecao
