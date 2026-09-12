"""Cliente HTTPS para o gateway de mensageria WhatsApp.

O endpoint e o token vêm de `config.json` (`WhatsAppConfig`) — nunca
hardcoded aqui. Implementa fila de retry com backoff exponencial para
absorver instabilidades pontuais da rede Wi-Fi local.
"""
import base64
import logging
import time
from typing import Optional

import httpx

from config.schemas import WhatsAppConfig
from src.behavior.event_engine import EventNotification

logger = logging.getLogger(__name__)


def encode_frame_jpeg_base64(frame) -> str:
    """Codifica um frame (array BGR do OpenCV) como JPEG em base64."""
    import cv2

    ok, buffer = cv2.imencode(".jpg", frame)
    if not ok:
        raise ValueError("Falha ao codificar o frame em JPEG.")
    return base64.b64encode(buffer).decode("ascii")


class WhatsAppNotifier:
    def __init__(self, config: WhatsAppConfig, client: Optional[httpx.Client] = None):
        self.config = config
        self._client = client or httpx.Client(timeout=10.0)

    def _build_payload(self, event: EventNotification, recipient_number: str, frame_jpeg_base64: Optional[str]) -> dict:
        payload = {
            "device_id": self.config.device_id,
            "recipient_number": recipient_number,
            "event_type": event.event_id,
            "severity": event.severity,
            "timestamp": event.timestamp.isoformat(),
            "message": f"[ALERTA SMA-TR] {event.name}: {event.message}",
            "voice_command_metadata": {"initiated_by_user": False, "transcribed_text": None},
        }
        if frame_jpeg_base64:
            payload["media_attachment"] = {"type": "image/jpeg", "base64_data": frame_jpeg_base64}
        return payload

    def send_event(
        self,
        event: EventNotification,
        recipient_number: str,
        frame_jpeg_base64: Optional[str] = None,
    ) -> bool:
        if not self.config.endpoint:
            logger.warning("WhatsApp endpoint nao configurado; evento %s nao enviado.", event.event_id)
            return False

        payload = self._build_payload(event, recipient_number, frame_jpeg_base64)
        headers = {"Content-Type": "application/json"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"

        delay = self.config.backoff_base_seconds
        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self._client.post(self.config.endpoint, json=payload, headers=headers)
                response.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "Falha ao enviar WhatsApp (tentativa %d/%d) para evento %s: %s",
                    attempt, self.config.max_retries, event.event_id, exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(delay)
                    delay *= 2

        logger.error(
            "Nao foi possivel enviar o evento %s apos %d tentativas. Ultimo erro: %s",
            event.event_id, self.config.max_retries, last_error,
        )
        return False

    def close(self):
        self._client.close()
