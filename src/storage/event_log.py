"""Histórico de eventos persistido em SQLite — mitiga um gap real (não
previsto no plano original): antes desta etapa, cada `EventNotification`
disparado só aparecia via `print()` no console de `main.py`, sem nenhum
registro persistente. Sem isso não há histórico para auditoria, para
reconstruir "o que aconteceu enquanto ninguém olhava", nem dado para a aba
de histórico da GUI ou para o servidor remoto (`src/server/remote_server.py`).

Por que SQLite e não JSON Lines (formato usado até esta etapa): o histórico
passou a ser consultado por período (retenção/expurgo) e por janela recente
(GUI, servidor remoto) — em JSONL isso significa reler o arquivo inteiro e
filtrar em Python a cada consulta, e o expurgo exigia reescrever o arquivo
inteiro. SQLite resolve os dois com um índice em `timestamp_epoch`, sem
deixar de ser "um único arquivo local" (`data/events.db`) — nenhum serviço
novo para administrar, continua condizente com a filosofia edge-first do
projeto.

Retenção/expurgo: `purge_older_than()` remove do banco os registros mais
antigos que uma janela configurável (`storage.retention_hours` em
config.json, ver config/schemas.py), e `start_auto_purge()` roda isso
periodicamente numa thread própria — relevante tanto para espaço em disco
numa instalação de longa duração quanto para retenção de dados sensíveis à
luz da LGPD.

Migração automática: se `path` apontar para um arquivo de uma instalação
anterior a esta migração (JSON Lines, texto puro), ou se existir um
`events.jsonl` legado ao lado do novo caminho padrão, os eventos são
importados para o SQLite na primeira inicialização e o arquivo original é
preservado como backup (`<arquivo>.bak`) — ver `_migrate_legacy_jsonl_if_needed`.
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import List, Optional

DEFAULT_LOG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "events.db")
)
LOG_PATH_ENV_VAR = "KINESIS_EVENT_LOG"

_SQLITE_HEADER = b"SQLite format 3\x00"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    severity TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    timestamp_epoch REAL NOT NULL,
    source_name TEXT NOT NULL,
    message TEXT NOT NULL,
    frame_base64 TEXT,
    person_label TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_timestamp_epoch ON events (timestamp_epoch);
"""

_SELECT_COLUMNS = (
    "event_id, category, name, severity, timestamp, source_name, message, frame_base64, person_label"
)


class EventLogger:
    def __init__(self, path: str = None):
        self.path = path or os.environ.get(LOG_PATH_ENV_VAR, DEFAULT_LOG_PATH)
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        self._migrate_legacy_jsonl_if_needed()

        conn = self._connect()
        try:
            self._ensure_schema(conn)
            conn.commit()
        finally:
            conn.close()

        self._purge_thread: Optional[threading.Thread] = None
        self._purge_stop_event = threading.Event()

    # --- infraestrutura SQLite ---

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA)

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        keys = ("event_id", "category", "name", "severity", "timestamp", "source_name", "message",
                "frame_base64", "person_label")
        return dict(zip(keys, row))

    @staticmethod
    def _insert_record(conn: sqlite3.Connection, record: dict) -> None:
        timestamp_epoch = datetime.fromisoformat(record["timestamp"]).timestamp()
        conn.execute(
            "INSERT INTO events (event_id, category, name, severity, timestamp, timestamp_epoch, "
            "source_name, message, frame_base64, person_label) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record["event_id"], record["category"], record["name"], record["severity"],
                record["timestamp"], timestamp_epoch, record["source_name"], record["message"],
                record.get("frame_base64"), record.get("person_label"),
            ),
        )

    # --- migração do formato legado (JSON Lines) ---

    @staticmethod
    def _is_sqlite_file(path: str) -> bool:
        try:
            with open(path, "rb") as f:
                return f.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
        except OSError:
            return False

    @staticmethod
    def _read_jsonl_events(path: str) -> List[dict]:
        events = []
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if raw_line:
                    events.append(json.loads(raw_line))
        return events

    def _find_legacy_jsonl_source(self) -> Optional[str]:
        """Duas situações possíveis de instalação anterior a esta migração:
        (1) `self.path` (o caminho configurado via `KINESIS_EVENT_LOG` ou
        `path=`) já existe, mas com conteúdo JSON Lines em vez de SQLite; ou
        (2) `self.path` ainda não existe (instalação nova usando o novo
        default `events.db`), mas um `events.jsonl` legado está presente ao
        lado dele, do tempo em que esse era o nome padrão."""
        if os.path.exists(self.path) and os.path.getsize(self.path) > 0 and not self._is_sqlite_file(self.path):
            return self.path

        if not os.path.exists(self.path):
            sibling = os.path.join(os.path.dirname(self.path), "events.jsonl")
            if sibling != self.path and os.path.exists(sibling) and os.path.getsize(sibling) > 0:
                return sibling

        return None

    def _migrate_legacy_jsonl_if_needed(self) -> None:
        source_path = self._find_legacy_jsonl_source()
        if source_path is None:
            return

        legacy_events = self._read_jsonl_events(source_path)
        backup_path = source_path + ".bak"
        os.replace(source_path, backup_path)

        conn = self._connect()
        try:
            self._ensure_schema(conn)
            for record in legacy_events:
                self._insert_record(conn, record)
            conn.commit()
        finally:
            conn.close()

        print(
            f"EventLogger: migrados {len(legacy_events)} evento(s) de um historico JSONL legado "
            f"('{source_path}') para SQLite em '{self.path}'. Backup do arquivo original: '{backup_path}'."
        )

    # --- API publica (compativel com a versao anterior baseada em JSONL) ---

    def log(self, event) -> None:
        """Aceita um EventNotification (ou qualquer objeto Pydantic com
        `.model_dump(mode="json")`)."""
        record = event.model_dump(mode="json")
        with self._lock:
            conn = self._connect()
            try:
                self._insert_record(conn, record)
                conn.commit()
            finally:
                conn.close()

    def read_all(self) -> List[dict]:
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM events ORDER BY timestamp_epoch ASC, id ASC"
                ).fetchall()
            finally:
                conn.close()
        return [self._row_to_dict(row) for row in rows]

    def read_recent(self, limit: Optional[int] = None) -> List[dict]:
        """Como `read_all()`, mas devolve só os `limit` registros mais
        recentes (ordem cronológica, do mais antigo para o mais novo) —
        usado tanto pela GUI local quanto pelo servidor remoto para não
        transferir o histórico inteiro a cada consulta."""
        query = f"SELECT {_SELECT_COLUMNS} FROM events ORDER BY timestamp_epoch DESC, id DESC"
        params: tuple = ()
        if limit is not None and limit >= 0:
            query += " LIMIT ?"
            params = (limit,)

        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(query, params).fetchall()
            finally:
                conn.close()

        rows.reverse()
        return [self._row_to_dict(row) for row in rows]

    def purge_older_than(self, retention_hours: float, now: Optional[datetime] = None) -> int:
        """Remove do banco os registros com `timestamp` mais antigo que
        `retention_hours`. Retorna quantos registros foram removidos."""
        now = now or datetime.now(timezone.utc)
        cutoff_epoch = (now - timedelta(hours=retention_hours)).timestamp()

        with self._lock:
            conn = self._connect()
            try:
                cursor = conn.execute("DELETE FROM events WHERE timestamp_epoch < ?", (cutoff_epoch,))
                conn.commit()
                removed = cursor.rowcount
            finally:
                conn.close()

        return max(removed, 0)

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
