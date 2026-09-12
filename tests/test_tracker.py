"""Testes de BehaviorTracker._analyze_tremor (EVT-11): injeta uma
trajetória sintética do pulso com frequência conhecida (sem precisar
construir landmarks MediaPipe falsos) e verifica se a FFT recupera essa
frequência dentro de uma tolerância razoável."""
import math

from src.behavior.tracker import BehaviorTracker


def _feed_sinusoidal_wrist(tracker, frequency_hz, amplitude_px, duration_seconds, sample_rate_hz, start_time=1000.0):
    dt = 1.0 / sample_rate_hz
    n_samples = int(duration_seconds * sample_rate_hz)
    last_now = start_time
    for i in range(n_samples):
        t = i * dt
        now = start_time + t
        y = 500.0 + amplitude_px * math.sin(2 * math.pi * frequency_hz * t)
        tracker.wrist_track_history.append((now, 500.0, y))
        last_now = now
    return last_now


def test_analyze_tremor_recovers_known_frequency():
    tracker = BehaviorTracker(tremor_window_seconds=4.0)
    now = _feed_sinusoidal_wrist(tracker, frequency_hz=4.0, amplitude_px=30.0, duration_seconds=4.0, sample_rate_hz=30.0)

    dominant_hz, amplitude = tracker._analyze_tremor(now, torso_height=300.0)

    assert dominant_hz is not None
    assert abs(dominant_hz - 4.0) < 0.5
    assert amplitude > 0.05


def test_analyze_tremor_recovers_a_different_known_frequency():
    tracker = BehaviorTracker(tremor_window_seconds=2.0)
    now = _feed_sinusoidal_wrist(tracker, frequency_hz=1.0, amplitude_px=40.0, duration_seconds=2.0, sample_rate_hz=30.0)

    dominant_hz, _ = tracker._analyze_tremor(now, torso_height=300.0)

    assert dominant_hz is not None
    assert abs(dominant_hz - 1.0) < 0.5


def test_analyze_tremor_returns_none_with_too_few_samples():
    tracker = BehaviorTracker()
    tracker.wrist_track_history.append((1000.0, 100.0, 100.0))

    dominant_hz, amplitude = tracker._analyze_tremor(1000.0, torso_height=300.0)

    assert dominant_hz is None
    assert amplitude == 0.0


def test_analyze_tremor_returns_none_with_zero_torso_height():
    tracker = BehaviorTracker()
    now = _feed_sinusoidal_wrist(tracker, frequency_hz=4.0, amplitude_px=30.0, duration_seconds=4.0, sample_rate_hz=30.0)

    dominant_hz, amplitude = tracker._analyze_tremor(now, torso_height=0.0)

    assert dominant_hz is None
    assert amplitude == 0.0


def test_analyze_tremor_low_amplitude_signal_has_low_normalized_amplitude():
    tracker = BehaviorTracker()
    now = _feed_sinusoidal_wrist(tracker, frequency_hz=4.0, amplitude_px=0.5, duration_seconds=4.0, sample_rate_hz=30.0)

    _, amplitude = tracker._analyze_tremor(now, torso_height=300.0)

    assert amplitude < 0.05  # deslocamento pequeno demais para ser tremor real


def test_analyze_tremor_ignores_samples_outside_window():
    tracker = BehaviorTracker(tremor_window_seconds=2.0)
    old_now = _feed_sinusoidal_wrist(
        tracker, frequency_hz=4.0, amplitude_px=30.0, duration_seconds=4.0, sample_rate_hz=30.0, start_time=0.0,
    )
    recent_now = _feed_sinusoidal_wrist(
        tracker, frequency_hz=1.0, amplitude_px=30.0, duration_seconds=2.0, sample_rate_hz=30.0,
        start_time=old_now + 100.0,
    )

    dominant_hz, _ = tracker._analyze_tremor(recent_now, torso_height=300.0)

    assert dominant_hz is not None
    assert abs(dominant_hz - 1.0) < 0.5


def test_analyze_pose_populates_tremor_fields():
    """Integração mínima: analyze_pose (chamado com landmarks reais de
    pose) deve propagar tremor_hz/tremor_amplitude no dict retornado, sem
    quebrar quando ainda não há amostras suficientes na janela."""
    import numpy as np

    class FakeLandmark:
        def __init__(self, x, y):
            self.x = x
            self.y = y

    # 33 landmarks minimos (indices usados por analyze_pose), parados em pe.
    landmarks = [FakeLandmark(0.5, 0.5) for _ in range(33)]
    landmarks[0] = FakeLandmark(0.5, 0.2)    # nose
    landmarks[11] = FakeLandmark(0.4, 0.3)   # left shoulder
    landmarks[12] = FakeLandmark(0.6, 0.3)   # right shoulder
    landmarks[23] = FakeLandmark(0.45, 0.6)  # left hip
    landmarks[24] = FakeLandmark(0.55, 0.6)  # right hip
    landmarks[25] = FakeLandmark(0.45, 0.8)  # left knee
    landmarks[26] = FakeLandmark(0.55, 0.8)  # right knee
    landmarks[15] = FakeLandmark(0.35, 0.5)  # left wrist
    landmarks[16] = FakeLandmark(0.65, 0.5)  # right wrist

    tracker = BehaviorTracker()
    result = tracker.analyze_pose(landmarks, frame_w=640, frame_h=480)

    assert "tremor_hz" in result
    assert "tremor_amplitude" in result
    assert result["tremor_hz"] is None  # so uma amostra ainda - sem dado suficiente
