"""Orquestra captura de áudio -> transcrição -> identificação de intenção
-> despacho de notificação, para UMA fonte de câmera/microfone.

Roda em sua própria thread, desacoplado do laço de vídeo (`CameraPipeline`):
o áudio é continuamente capturado e transcrito em janelas de
`chunk_seconds`, sem depender do ritmo de `process_next_frame()`.
"""
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

from src.audio.camera_audio_capture import CameraAudioCapture
from src.audio.intent_matcher import EmergencyIntentMatcher, VoiceIntent, normalize
from src.audio.transcriber import SpeechTranscriber
from src.behavior.event_engine import EventNotification

VOICE_EVENT_CATALOG: Dict[str, Dict[str, str]] = {
    "VOZ-SOCORRO": {"category": "Voz", "name": "Comando de Voz: Pedido de Ajuda", "default_severity": "CRITICAL"},
    "VOZ-CHAME-ME": {"category": "Voz", "name": "Comando de Voz: Pedido para Ligar", "default_severity": "HIGH"},
    "VOZ-MENSAGEM": {"category": "Voz", "name": "Comando de Voz: Enviar Mensagem", "default_severity": "MEDIUM"},
    "VOZ-CHAMAR-CONTATO": {"category": "Voz", "name": "Comando de Voz: Chamar Contato", "default_severity": "MEDIUM"},
}

_EVENT_ID_BY_INTENT = {
    "HELP": "VOZ-SOCORRO",
    "CALL_ME": "VOZ-CHAME-ME",
    "SEND_MESSAGE": "VOZ-MENSAGEM",
    "CALL_CONTACT": "VOZ-CHAMAR-CONTATO",
}


def build_voice_event(intent: VoiceIntent, source_name: str) -> EventNotification:
    event_id = _EVENT_ID_BY_INTENT[intent.intent_type]
    meta = VOICE_EVENT_CATALOG[event_id]
    return EventNotification(
        event_id=event_id, category=meta["category"], name=meta["name"],
        severity=meta["default_severity"], timestamp=datetime.now(timezone.utc),
        source_name=source_name, message=f'Comando de voz detectado: "{intent.matched_text}"',
    )


def resolve_contact(contacts, spoken_name: str):
    """Casamento tolerante (sem acento/case, substring) entre o nome dito
    e os contatos cadastrados — o ASR raramente transcreve nomes próprios
    com grafia perfeita."""
    if not spoken_name:
        return None
    norm_spoken = normalize(spoken_name)
    for contact in contacts.contacts:
        norm_contact = normalize(contact.name)
        if norm_spoken == norm_contact or norm_spoken in norm_contact or norm_contact in norm_spoken:
            return contact
    return None


class VoiceController:
    def __init__(
        self, source_name: str, audio_source, contacts, dispatcher, event_logger,
        transcriber: Optional[SpeechTranscriber] = None, language: str = "pt", chunk_seconds: float = 4.0,
    ):
        self.source_name = source_name
        self.contacts = contacts
        self.dispatcher = dispatcher
        self.event_logger = event_logger
        self.transcriber = transcriber or SpeechTranscriber(language=language)
        self.matcher = EmergencyIntentMatcher()
        self.capture = CameraAudioCapture(audio_source, chunk_seconds=chunk_seconds)
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self):
        if self._running:
            return self
        self._running = True
        self.capture.start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while self._running:
            chunk = self.capture.get_chunk(timeout=1.0)
            if chunk is None:
                continue
            try:
                text = self.transcriber.transcribe(chunk)
            except Exception:
                continue  # modelo indisponivel/erro de inferencia: nao derruba o controller
            if not text:
                continue
            intent = self.matcher.match(text)
            if intent is not None:
                self.handle_intent(intent)

    def handle_intent(self, intent: VoiceIntent):
        event = build_voice_event(intent, self.source_name)
        self.event_logger.log(event)

        if intent.contact_name:
            contact = resolve_contact(self.contacts, intent.contact_name)
            if contact is not None:
                self.dispatcher.dispatch_to_contact(event, contact.id)
                return
            if intent.intent_type == "CALL_CONTACT":
                # nome dito nao bate com nenhum contato cadastrado: nao ha
                # como saber a quem entregar - melhor nao agir do que
                # notificar o destinatario errado.
                return
            # SEND_MESSAGE com nome nao resolvido: cai para o broadcast
            # generico abaixo (destinatarios configurados em config.json).

        self.dispatcher.dispatch(event, frame_b64=None)

    def stop(self):
        self._running = False
        self.capture.stop()
