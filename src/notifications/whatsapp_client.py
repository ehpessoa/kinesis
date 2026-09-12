"""Cliente HTTPS para a Evolution API (gateway de mensageria WhatsApp,
instância "senszia" — ver documentação da API fornecida pelo usuário).

O endpoint (base URL) e a instância vêm de `config.json` (`WhatsAppConfig`) -
não são segredo. A apikey NUNCA vem de config.json/GUI: é lida de
EVOLUTION_API_KEY (variável de ambiente, tipicamente num `.env` - ver
.env.example), para não guardar o segredo num arquivo que a GUI escreve em
texto puro. Implementa fila de retry com backoff exponencial para absorver
instabilidades pontuais da rede Wi-Fi local.
"""
import base64
import logging
import os
import re
import time
from typing import Optional

import httpx
from dotenv import load_dotenv

from config.schemas import WhatsAppConfig
from src.behavior.event_engine import EventNotification

load_dotenv()

logger = logging.getLogger(__name__)

EVOLUTION_API_KEY_ENV_VAR = "EVOLUTION_API_KEY"
SEND_TEXT_PATH = "/message/sendText/{instance}"


def encode_frame_jpeg_bytes(frame) -> bytes:
    """Codifica um frame (array BGR do OpenCV) como bytes JPEG crus — usado
    onde base64 seria um passo desnecessário (ex: servir a imagem via
    HTTP, ver src/server/remote_server.py)."""
    import cv2

    ok, buffer = cv2.imencode(".jpg", frame)
    if not ok:
        raise ValueError("Falha ao codificar o frame em JPEG.")
    return buffer.tobytes()


def encode_frame_jpeg_base64(frame) -> str:
    """Codifica um frame (array BGR do OpenCV) como JPEG em base64."""
    return base64.b64encode(encode_frame_jpeg_bytes(frame)).decode("ascii")


def _normalize_number(number: str) -> str:
    """Remove tudo que nao for digito (+, espacos, parenteses, traco) - a
    Evolution API espera soh DDI+DDD+numero, ex: 5511999998888."""
    return re.sub(r"\D", "", number or "")


class WhatsAppNotifier:
    def __init__(self, config: WhatsAppConfig, client: Optional[httpx.Client] = None, api_key: Optional[str] = None):
        self.config = config
        self._client = client or httpx.Client(timeout=10.0)
        # api_key soh eh passado explicitamente em teste; em producao vem do
        # ambiente, lido no momento do envio (nao no __init__) para que um
        # .env carregado depois da instanciacao do notifier ainda funcione.
        self._api_key_override = api_key

    def _api_key(self) -> Optional[str]:
        if self._api_key_override is not None:
            return self._api_key_override
        return os.environ.get(EVOLUTION_API_KEY_ENV_VAR)

    def _build_payload(self, event: EventNotification, recipient_number: str) -> dict:
        who = f" ({event.person_label})" if event.person_label else ""
        text = f"[ALERTA SMA-TR] {event.name}{who}: {event.message}"
        return {"number": _normalize_number(recipient_number), "text": text}

    def send_event(
        self,
        event: EventNotification,
        recipient_number: str,
        frame_jpeg_base64: Optional[str] = None,
    ) -> bool:
        # frame_jpeg_base64 fica sem uso por enquanto: o endpoint sendText da
        # Evolution API nao aceita midia (isso exigiria /message/sendMedia/
        # {instance}, um endpoint separado - ver README, extensao futura).
        if not self.config.endpoint or not self.config.instance:
            logger.warning(
                "Evolution API nao configurada (endpoint/instancia); evento %s nao enviado.", event.event_id,
            )
            return False
        api_key = self._api_key()
        if not api_key:
            logger.warning(
                "%s nao definida no ambiente; evento %s nao enviado.", EVOLUTION_API_KEY_ENV_VAR, event.event_id,
            )
            return False

        url = self.config.endpoint.rstrip("/") + SEND_TEXT_PATH.format(instance=self.config.instance)
        payload = self._build_payload(event, recipient_number)
        headers = {"Content-Type": "application/json", "apikey": api_key}

        delay = self.config.backoff_base_seconds
        last_error = None
        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = self._client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                return True
            except httpx.HTTPError as exc:
                last_error = exc
                response_body = ""
                if isinstance(exc, httpx.HTTPStatusError):
                    response_body = f" | resposta: {exc.response.text}"
                logger.warning(
                    "Falha ao enviar WhatsApp (tentativa %d/%d) para evento %s: %s%s",
                    attempt, self.config.max_retries, event.event_id, exc, response_body,
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
