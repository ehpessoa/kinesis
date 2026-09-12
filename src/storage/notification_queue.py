"""Fila de notificações WhatsApp pendentes, persistida em disco — mitiga um
gap real (não previsto no plano original): o retry com backoff exponencial
de `WhatsAppNotifier` (ver src/notifications/whatsapp_client.py) roda
inteiramente em memória, numa thread. Se o processo caísse no meio de um
backoff — ou mesmo depois de esgotar as tentativas —, a notificação se
perdia sem nenhum registro, e ninguém saberia que um alerta de queda não
chegou ao cuidador.

Cada notificação é gravada ANTES da tentativa de envio e só é removida da
fila após confirmação de entrega (HTTP 2xx). Uma falha do processo a
qualquer momento — durante o backoff, ou por esgotar as tentativas — deixa
o registro no arquivo, pronto para reenvio na próxima inicialização via
`redeliver_pending()` (chamado no startup de main.py/gui_main.py).
"""
import json
import os
import threading
import uuid
from typing import Dict, Optional

DEFAULT_QUEUE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "pending_notifications.json")
)
QUEUE_PATH_ENV_VAR = "KINESIS_PENDING_QUEUE"


class PendingNotificationQueue:
    def __init__(self, path: str = None):
        self.path = path or os.environ.get(QUEUE_PATH_ENV_VAR, DEFAULT_QUEUE_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            self._write({})

    def _read(self) -> Dict[str, dict]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write(self, data: Dict[str, dict]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def enqueue(self, event_json: dict, recipient_number: str, frame_b64: Optional[str]) -> str:
        record_id = uuid.uuid4().hex
        with self._lock:
            data = self._read()
            data[record_id] = {
                "event": event_json,
                "recipient_number": recipient_number,
                "frame_b64": frame_b64,
            }
            self._write(data)
        return record_id

    def mark_delivered(self, record_id: str) -> None:
        with self._lock:
            data = self._read()
            data.pop(record_id, None)
            self._write(data)

    def list_pending(self) -> Dict[str, dict]:
        with self._lock:
            return self._read()
