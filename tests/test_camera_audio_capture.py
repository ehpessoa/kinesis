"""Testa a lógica de janelamento/normalização de PCM sem depender de
hardware de áudio ou de uma câmera RTSP real (ver
src/audio/camera_audio_capture.py para o porquê a extração real via
ffmpeg não pôde ser validada contra uma câmera de verdade neste ambiente —
foi validada contra um arquivo local, que exercita a mesma decodificação)."""
import subprocess

import numpy as np

from src.audio.camera_audio_capture import CameraAudioCapture


def test_chunk_bytes_matches_chunk_seconds_and_sample_rate():
    cap = CameraAudioCapture(source="rtsp://fake", chunk_seconds=2.0, sample_rate=16000)
    assert cap._chunk_bytes == 2.0 * 16000 * 2  # s16le = 2 bytes/amostra


def test_push_pcm_bytes_normalizes_int16_to_float32():
    cap = CameraAudioCapture(source="rtsp://fake", chunk_seconds=2.0, sample_rate=16000)
    samples = np.full(32000, 16384, dtype=np.int16)  # ~metade do range int16
    cap.push_pcm_bytes(samples.tobytes())

    chunk = cap.get_chunk(timeout=0.1)
    assert chunk is not None
    assert chunk.dtype == np.float32
    assert len(chunk) == 32000
    assert abs(chunk[0] - 0.5) < 1e-3


def test_incomplete_chunk_does_not_enqueue_prematurely():
    cap = CameraAudioCapture(source="rtsp://fake", chunk_seconds=2.0, sample_rate=16000)
    cap.push_pcm_bytes(b"\x00\x01" * 100)  # bem menos que um chunk completo
    assert cap.get_chunk(timeout=0.1) is None


def test_multiple_complete_chunks_are_enqueued_without_premature_leftover():
    cap = CameraAudioCapture(source="rtsp://fake", chunk_seconds=1.0, sample_rate=16000)  # 32000 bytes/chunk
    cap.push_pcm_bytes(b"\x00\x01" * 32000)  # exatamente 2 chunks completos

    assert cap.get_chunk(timeout=0.1) is not None
    assert cap.get_chunk(timeout=0.1) is not None
    assert cap.get_chunk(timeout=0.1) is None


def test_is_rtsp_or_file_source_distinguishes_string_from_int():
    assert CameraAudioCapture(source="rtsp://x").is_rtsp_or_file_source() is True
    assert CameraAudioCapture(source=0).is_rtsp_or_file_source() is False


def test_ffmpeg_available_in_this_environment():
    """Sanidade do ambiente de teste: se ffmpeg nao estiver instalado, a
    captura via RTSP degrada silenciosamente (por design), mas o teste de
    integracao real abaixo depende dele."""
    result = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    assert result.returncode == 0


def test_real_ffmpeg_extraction_from_local_wav_file(tmp_path):
    """Sintetiza um WAV com espeak-ng e extrai PCM via ffmpeg de verdade -
    o mesmo mecanismo usado para audio de camera RTSP, so a fonte de
    transporte e um arquivo local em vez de uma URL rtsp://."""
    wav_path = tmp_path / "speech.wav"
    result = subprocess.run(
        ["espeak-ng", "-v", "pt-br", "teste de audio", "-w", str(wav_path)],
        capture_output=True,
    )
    if result.returncode != 0 or not wav_path.exists():
        import pytest
        pytest.skip("espeak-ng nao disponivel neste ambiente para gerar audio de teste")

    cap = CameraAudioCapture(source=str(wav_path), chunk_seconds=0.5, sample_rate=16000)
    cap.start()

    import time
    chunks = []
    deadline = time.time() + 5
    while time.time() < deadline:
        chunk = cap.get_chunk(timeout=0.5)
        if chunk is not None:
            chunks.append(chunk)
        elif cap._process is not None and cap._process.poll() is not None:
            break
    cap.stop()

    assert len(chunks) >= 1
    for chunk in chunks:
        assert chunk.dtype == np.float32
        assert chunk.min() >= -1.0 and chunk.max() <= 1.0
