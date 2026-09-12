"""Log de eventos em JSONL — mitiga um gap real (não previsto no plano
original): antes desta etapa, cada `EventNotification` disparado só
aparecia via `print()` no console de `main.py`, sem nenhum registro
persistente. Sem isso não há histórico para auditoria, para reconstruir
"o que aconteceu enquanto ninguém olhava", nem dado para uma futura aba de
histórico na GUI (que hoje só mostra o feed de alertas em memória, perdido
ao fechar a janela).

Formato: um objeto JSON por linha (JSON Lines), somente-anexação. Não há
rotação/retenção automática — para uma instalação de longa duração, girar
ou compactar `data/events.jsonl` periodicamente é responsabilidade externa
(ex: logrotate), fora do escopo desta mitigação.
"""
import json
import os
import threading
from typing import List

DEFAULT_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "events.jsonl")
)
LOG_PATH_ENV_VAR = "KINESIS_EVENT_LOG"


class EventLogger:
    def __init__(self, path: str = None):
        self.path = path or os.environ.get(LOG_PATH_ENV_VAR, DEFAULT_LOG_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def log(self, event) -> None:
        """Aceita um EventNotification (ou qualquer objeto Pydantic com
        `.model_dump_json()`)."""
        line = event.model_dump_json()
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_all(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        events = []
        with self._lock:
            with open(self.path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    raw_line = raw_line.strip()
                    if raw_line:
                        events.append(json.loads(raw_line))
        return events
