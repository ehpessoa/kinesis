"""SpeechTranscriber carrega o modelo de forma tardia (só na 1ª chamada de
`transcribe`) — por isso construir a classe não faz nenhuma chamada de
rede, o que é testável sem depender do host (Hugging Face Hub) que está
bloqueado na rede usada para construir este projeto (ver README)."""
import os

import pytest

from src.audio.transcriber import SpeechTranscriber


def test_construction_does_not_load_model():
    transcriber = SpeechTranscriber(model_size="small", language="pt")
    assert transcriber._model is None  # nenhum carregamento ainda


def test_model_dir_overrides_model_size_as_path():
    transcriber = SpeechTranscriber(model_size="small", model_dir="/algum/caminho/local")
    assert transcriber._model_size_or_path == "/algum/caminho/local"


def test_transcribe_uses_injected_model_without_downloading():
    """Confirma o contrato entre VoiceController e SpeechTranscriber sem
    depender do faster-whisper real: injeta um "modelo" falso no lugar de
    `_model` para pular `_ensure_loaded` (que faria o download real)."""
    transcriber = SpeechTranscriber()

    class FakeSegment:
        def __init__(self, text):
            self.text = text

    class FakeModel:
        def transcribe(self, audio, language=None):
            return [FakeSegment(" socorro "), FakeSegment("preciso de ajuda")], None

    transcriber._model = FakeModel()
    text = transcriber.transcribe(audio=None)
    assert text == "socorro preciso de ajuda"


_MODEL_DIR = os.environ.get("KINESIS_WHISPER_MODEL_DIR")
requires_real_model = pytest.mark.skipif(
    not _MODEL_DIR or not os.path.isdir(_MODEL_DIR),
    reason="nenhum modelo faster-whisper real disponivel (defina KINESIS_WHISPER_MODEL_DIR apontando para um "
           "modelo CTranslate2 pre-convertido para habilitar este teste - ver README)",
)


@requires_real_model
def test_transcribe_with_real_model_and_synthesized_speech(tmp_path):
    import subprocess
    wav_path = tmp_path / "speech.wav"
    subprocess.run(["espeak-ng", "-v", "pt-br", "socorro", "-w", str(wav_path)], check=True)

    transcriber = SpeechTranscriber(model_dir=_MODEL_DIR, language="pt")
    text = transcriber.transcribe(str(wav_path))
    assert "socorro" in text.lower()
