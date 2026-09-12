"""Transcrição de fala para texto (ASR), via `faster-whisper`, rodando
localmente após o modelo estar disponível.

O carregamento é feito de forma tardia (`_ensure_loaded`, na primeira
chamada real de `transcribe`) — construir um `SpeechTranscriber` não baixa
nem carrega nada, o que permite testar o resto do módulo de voz sem
depender de rede ou de um modelo real.

O download automático padrão do `faster-whisper` usa o Hugging Face Hub
(`huggingface.co`). Em uma rede que bloqueie esse host — como o ambiente
usado para construir este projeto, onde isso foi confirmado — passe
`model_dir` apontando para uma pasta com um modelo já convertido para o
formato CTranslate2 (baixado manualmente em uma máquina com acesso; ver
README para instruções), evitando qualquer dependência de rede em tempo de
execução.
"""
from typing import Optional

import numpy as np


class SpeechTranscriber:
    def __init__(
        self, model_size: str = "small", language: str = "pt",
        model_dir: Optional[str] = None, device: str = "cpu", compute_type: str = "int8",
    ):
        self.language = language
        self._model_size_or_path = model_dir or model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _ensure_loaded(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self._model_size_or_path, device=self._device, compute_type=self._compute_type)

    def transcribe(self, audio: np.ndarray) -> str:
        """`audio`: PCM mono float32 normalizado em [-1, 1], 16kHz (mesmo
        formato produzido por CameraAudioCapture.get_chunk)."""
        self._ensure_loaded()
        segments, _ = self._model.transcribe(audio, language=self.language)
        return " ".join(seg.text.strip() for seg in segments).strip()
