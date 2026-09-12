"""Log de eventos em JSONL — mitiga um gap real (não previsto no plano
original): antes desta etapa, cada `EventNotification` disparado só
aparecia via `print()` no console de `main.py`, sem nenhum registro
persistente. Sem isso não há histórico para auditoria, para reconstruir
"o que aconteceu enquanto ninguém olhava", nem dado para uma futura aba de
histórico na GUI (que hoje só mostra o feed de alertas em memória, perdido
ao fechar a janela).

Formato: um objeto JSON por linha (JSON Lines), somente-anexação.

Retenção/expurgo: `purge_older_than()` remove do arquivo os registros mais
antigos que uma janela configurável (`storage.retention_hours` em
config.json, ver config/schemas.py), e `start_auto_purge()` roda isso
periodicamente numa thread própria — mitigação do gap "log cresce
indefinidamente" citado no roteiro de evolução (relevante tanto para
espaço em disco numa instalação de longa duração quanto para retenção de
dados sensíveis à luz da LGPD).
"""
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DEFAULT_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "events.jsonl")
)
LOG_PATH_ENV_VAR = "KINESIS_EVENT_LOG"


class EventLogger:
    def __init__(self, path: str = None):
        self.path = path or os.environ.get(LOG_PATH_ENV_VAR, DEFAULT_LOG_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        self._purge_thread: Optional[threading.Thread] = None
        self._purge_stop_event = threading.Event()

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

    def read_recent(self, limit: Optional[int] = None) -> List[dict]:
        """Como `read_all()`, mas devolve só os `limit` registros mais
        recentes (ordem cronológica, do mais antigo para o mais novo) —
        usado tanto pela GUI local quanto pelo servidor remoto para não
        transferir o log inteiro a cada consulta."""
        events = self.read_all()
        if limit is not None and limit >= 0:
            events = events[-limit:]
        return events

    def purge_older_than(self, retention_hours: float, now: Optional[datetime] = None) -> int:
        """Remove do log, em uma única reescrita atômica do arquivo, os
        registros com `timestamp` mais antigo que `retention_hours`.
        Retorna quantos registros foram removidos. Sem efeito se o arquivo
        ainda não existe ou se nada está fora da janela de retenção."""
        if not os.path.exists(self.path):
            return 0
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=retention_hours)

        with self._lock:
            kept_lines = []
            removed = 0
            with open(self.path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    record = json.loads(stripped)
                    timestamp = datetime.fromisoformat(record["timestamp"])
                    if timestamp >= cutoff:
                        kept_lines.append(stripped)
                    else:
                        removed += 1

            if removed:
                tmp_path = self.path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    for line in kept_lines:
                        f.write(line + "\n")
                os.replace(tmp_path, self.path)

        return removed

    def start_auto_purge(self, retention_hours: float, check_interval_seconds: float = 3600.0) -> None:
        """Inicia uma thread daemon que chama `purge_older_than()` a cada
        `check_interval_seconds` — chamado uma vez no startup de
        main.py/gui_main.py. Idempotente: uma segunda chamada sem antes
        parar a primeira (`stop_auto_purge()`) não faz nada."""
        if self._purge_thread is not None:
            return
        self._purge_stop_event.clear()

        def _loop():
            while not self._purge_stop_event.wait(check_interval_seconds):
                try:
                    removed = self.purge_older_than(retention_hours)
                    if removed:
                        print(f"EventLogger: expurgado(s) {removed} evento(s) com mais de {retention_hours:.0f}h de historico.")
                except Exception as exc:
                    print(f"EventLogger: falha ao expurgar historico automaticamente: {exc}")

        self._purge_thread = threading.Thread(target=_loop, daemon=True)
        self._purge_thread.start()

    def stop_auto_purge(self) -> None:
        if self._purge_thread is not None:
            self._purge_stop_event.set()
            self._purge_thread.join(timeout=2.0)
            self._purge_thread = None
