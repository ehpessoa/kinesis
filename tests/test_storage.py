import json
import time
from datetime import datetime, timedelta, timezone

import httpx

from config.schemas import AppConfig, CameraSourceConfig, Contact, ContactsFile, EventRuleConfig, WhatsAppConfig
from src.behavior.event_engine import EventNotification
from src.notifications.whatsapp_client import WhatsAppNotifier
from src.storage.event_log import EventLogger
from src.storage.notification_queue import PendingNotificationQueue


def make_event(**overrides):
    defaults = dict(
        event_id="EVT-01", category="Queda", name="Queda Brusca Detectada", severity="CRITICAL",
        timestamp=datetime.now(timezone.utc), source_name="Sala de Estar", message="Queda simulada.",
    )
    defaults.update(overrides)
    return EventNotification(**defaults)


# --- EventLogger ---

def test_event_logger_writes_and_reads_jsonl(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    logger.log(make_event(event_id="EVT-01"))
    logger.log(make_event(event_id="EVT-04", severity="MEDIUM"))

    logged = logger.read_all()
    assert len(logged) == 2
    assert logged[0]["event_id"] == "EVT-01"
    assert logged[1]["event_id"] == "EVT-04"


def test_event_logger_read_all_without_file_returns_empty(tmp_path):
    logger = EventLogger(path=str(tmp_path / "nao_existe.jsonl"))
    assert logger.read_all() == []


def test_events_are_persisted_in_sqlite(tmp_path):
    db_path = tmp_path / "events.db"
    logger = EventLogger(path=str(db_path))
    logger.log(make_event(event_id="EVT-01"))

    assert db_path.exists()
    with open(db_path, "rb") as f:
        assert f.read(16) == b"SQLite format 3\x00"


# --- Migracao automatica do formato legado (JSON Lines) ---

def test_migrates_legacy_jsonl_at_configured_path(tmp_path):
    legacy_path = tmp_path / "events.jsonl"
    legacy_path.write_text(
        make_event(event_id="EVT-01").model_dump_json() + "\n"
        + make_event(event_id="EVT-04", severity="MEDIUM").model_dump_json() + "\n",
        encoding="utf-8",
    )

    logger = EventLogger(path=str(legacy_path))

    migrated = logger.read_all()
    assert [e["event_id"] for e in migrated] == ["EVT-01", "EVT-04"]
    assert (tmp_path / "events.jsonl.bak").exists()
    # o caminho configurado agora contem o banco SQLite recriado, nao mais o JSONL original
    with open(legacy_path, "rb") as f:
        assert f.read(16) == b"SQLite format 3\x00"


def test_migrates_legacy_jsonl_sibling_when_using_new_default_db_path(tmp_path):
    legacy_sibling = tmp_path / "events.jsonl"
    legacy_sibling.write_text(make_event(event_id="EVT-06").model_dump_json() + "\n", encoding="utf-8")

    logger = EventLogger(path=str(tmp_path / "events.db"))

    migrated = logger.read_all()
    assert [e["event_id"] for e in migrated] == ["EVT-06"]
    assert (tmp_path / "events.jsonl.bak").exists()
    assert not legacy_sibling.exists()


def test_no_migration_when_no_legacy_file_present(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.db"))
    assert logger.read_all() == []
    assert not (tmp_path / "events.jsonl.bak").exists()


def test_read_recent_limits_to_last_n(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    for event_id in ("EVT-01", "EVT-02", "EVT-03", "EVT-04"):
        logger.log(make_event(event_id=event_id))

    recent = logger.read_recent(limit=2)

    assert [e["event_id"] for e in recent] == ["EVT-03", "EVT-04"]


def test_read_recent_without_limit_returns_everything(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    logger.log(make_event(event_id="EVT-01"))
    logger.log(make_event(event_id="EVT-02"))

    assert len(logger.read_recent()) == 2


# --- Expurgo/retenção (EventLogger.purge_older_than / start_auto_purge) ---

def test_purge_older_than_removes_only_expired_events(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    now = datetime.now(timezone.utc)
    logger.log(make_event(event_id="EVT-03", timestamp=now - timedelta(hours=30)))
    logger.log(make_event(event_id="EVT-01", timestamp=now - timedelta(hours=1)))

    removed = logger.purge_older_than(retention_hours=24, now=now)

    assert removed == 1
    remaining = logger.read_all()
    assert len(remaining) == 1
    assert remaining[0]["event_id"] == "EVT-01"


def test_purge_older_than_keeps_everything_when_nothing_expired(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    now = datetime.now(timezone.utc)
    logger.log(make_event(timestamp=now))

    removed = logger.purge_older_than(retention_hours=24, now=now)

    assert removed == 0
    assert len(logger.read_all()) == 1


def test_purge_older_than_without_file_is_noop(tmp_path):
    logger = EventLogger(path=str(tmp_path / "nao_existe.jsonl"))
    assert logger.purge_older_than(retention_hours=24) == 0


def test_start_auto_purge_runs_periodically_in_background(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    now = datetime.now(timezone.utc)
    logger.log(make_event(timestamp=now - timedelta(hours=48)))

    logger.start_auto_purge(retention_hours=24, check_interval_seconds=0.05)
    try:
        deadline = time.time() + 2.0
        while time.time() < deadline and logger.read_all():
            time.sleep(0.05)
        assert logger.read_all() == []
    finally:
        logger.stop_auto_purge()


def test_start_auto_purge_is_idempotent(tmp_path):
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    logger.start_auto_purge(retention_hours=24, check_interval_seconds=10.0)
    first_thread = logger._purge_thread
    logger.start_auto_purge(retention_hours=24, check_interval_seconds=10.0)
    assert logger._purge_thread is first_thread
    logger.stop_auto_purge()


# --- PendingNotificationQueue ---

def test_queue_enqueue_and_mark_delivered_removes_record(tmp_path):
    queue = PendingNotificationQueue(path=str(tmp_path / "pending.json"))
    record_id = queue.enqueue(make_event().model_dump(mode="json"), "5511999998888", "base64fake")

    assert len(queue.list_pending()) == 1
    queue.mark_delivered(record_id)
    assert queue.list_pending() == {}


def test_queue_simulated_crash_keeps_record_persisted(tmp_path):
    """Simula o processo caindo entre o enqueue e a confirmacao de entrega:
    uma segunda instancia da fila (mesmo arquivo) ainda ve o registro."""
    queue_path = str(tmp_path / "pending.json")
    queue = PendingNotificationQueue(path=queue_path)
    record_id = queue.enqueue(make_event(event_id="EVT-04").model_dump(mode="json"), "5511977776666", None)
    # processo "cai" aqui - nunca chama mark_delivered

    reopened_queue = PendingNotificationQueue(path=queue_path)
    pending = reopened_queue.list_pending()
    assert record_id in pending
    rebuilt_event = EventNotification.model_validate(pending[record_id]["event"])
    assert rebuilt_event.event_id == "EVT-04"


# --- NotificationDispatcher.redeliver_pending (integracao ponta a ponta) ---

def test_redeliver_pending_resends_and_clears_queue(tmp_path):
    from main import NotificationDispatcher  # import tardio: evita custo de import do main.py nos outros testes

    queue_path = str(tmp_path / "pending.json")
    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(json.loads(request.read()))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    whatsapp_config = WhatsAppConfig(endpoint="https://zap.example/send", max_retries=1)
    notifier = WhatsAppNotifier(whatsapp_config, client=client)

    app_config = AppConfig(
        cameras=[CameraSourceConfig(name="Teste", index=0)],
        events={"EVT-01": EventRuleConfig(enabled=True, notify_contact_ids=["filho_carlos"])},
        whatsapp=whatsapp_config,
    )
    contacts = ContactsFile(contacts=[Contact(id="filho_carlos", name="Carlos", whatsapp_number="5511999998888")])

    # Simula uma notificacao que ficou pendente de uma execucao anterior
    # (enfileirada, mas o processo "caiu" antes de confirmar a entrega).
    queue = PendingNotificationQueue(path=queue_path)
    queue.enqueue(make_event(event_id="EVT-01").model_dump(mode="json"), "5511999998888", None)

    # Nova instancia do dispatcher (simulando reinicio do processo), mesma fila em disco.
    dispatcher = NotificationDispatcher(app_config, contacts, notifier, queue=PendingNotificationQueue(path=queue_path))
    dispatcher.redeliver_pending()

    time.sleep(0.3)  # o reenvio roda em thread separada

    assert len(sent_payloads) == 1
    assert sent_payloads[0]["event_type"] == "EVT-01"
    assert dispatcher.queue.list_pending() == {}


def test_dispatch_persists_before_send_and_clears_after_success(tmp_path):
    from main import NotificationDispatcher

    queue_path = str(tmp_path / "pending.json")
    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(json.loads(request.read()))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    whatsapp_config = WhatsAppConfig(endpoint="https://zap.example/send", max_retries=1)
    notifier = WhatsAppNotifier(whatsapp_config, client=client)

    app_config = AppConfig(
        cameras=[CameraSourceConfig(name="Teste", index=0)],
        events={"EVT-01": EventRuleConfig(enabled=True, notify_contact_ids=["cuidadora_ana"])},
        whatsapp=whatsapp_config,
    )
    contacts = ContactsFile(contacts=[Contact(id="cuidadora_ana", name="Ana", whatsapp_number="5511977776666")])

    dispatcher = NotificationDispatcher(app_config, contacts, notifier, queue=PendingNotificationQueue(path=queue_path))
    dispatcher.dispatch(make_event(event_id="EVT-01"), frame_b64=None)

    time.sleep(0.3)

    assert len(sent_payloads) == 1
    assert dispatcher.queue.list_pending() == {}


def test_dispatch_ignores_disabled_or_unconfigured_events(tmp_path):
    from main import NotificationDispatcher

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    whatsapp_config = WhatsAppConfig(endpoint="https://zap.example/send")
    notifier = WhatsAppNotifier(whatsapp_config, client=client)

    app_config = AppConfig(
        cameras=[CameraSourceConfig(name="Teste", index=0)],
        events={"EVT-01": EventRuleConfig(enabled=False, notify_contact_ids=["cuidadora_ana"])},
        whatsapp=whatsapp_config,
    )
    contacts = ContactsFile(contacts=[Contact(id="cuidadora_ana", name="Ana", whatsapp_number="5511977776666")])
    dispatcher = NotificationDispatcher(app_config, contacts, notifier, queue=PendingNotificationQueue(path=str(tmp_path / "pending.json")))

    dispatcher.dispatch(make_event(event_id="EVT-01"), frame_b64=None)  # evento desabilitado
    dispatcher.dispatch(make_event(event_id="EVT-09"), frame_b64=None)  # evento nao configurado

    time.sleep(0.1)
    assert dispatcher.queue.list_pending() == {}


# --- NotificationDispatcher.dispatch_to_contact (usado pelo modulo de voz) ---

def _make_dispatcher(tmp_path, events=None):
    from main import NotificationDispatcher

    sent = []
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: (sent.append(json.loads(r.read())), httpx.Response(200))[1]
    ))
    whatsapp_config = WhatsAppConfig(endpoint="https://zap.example/send", max_retries=1)
    notifier = WhatsAppNotifier(whatsapp_config, client=client)
    app_config = AppConfig(
        cameras=[CameraSourceConfig(name="Teste", index=0)],
        whatsapp=whatsapp_config, events=events or {},
    )
    contacts = ContactsFile(contacts=[Contact(id="carlos", name="Carlos", whatsapp_number="5511999998888")])
    dispatcher = NotificationDispatcher(
        app_config, contacts, notifier, queue=PendingNotificationQueue(path=str(tmp_path / "pending.json"))
    )
    return dispatcher, sent


def test_dispatch_to_contact_sends_to_named_contact_directly(tmp_path):
    dispatcher, sent = _make_dispatcher(tmp_path)

    ok = dispatcher.dispatch_to_contact(make_event(event_id="VOZ-CHAMAR-CONTATO"), "carlos")

    assert ok is True
    time.sleep(0.3)
    assert len(sent) == 1
    assert sent[0]["recipient_number"] == "5511999998888"


def test_dispatch_to_contact_returns_false_for_unknown_contact(tmp_path):
    dispatcher, sent = _make_dispatcher(tmp_path)

    ok = dispatcher.dispatch_to_contact(make_event(event_id="VOZ-CHAMAR-CONTATO"), "nao_existe")

    assert ok is False
    assert sent == []


def test_dispatch_to_contact_respects_disabled_rule(tmp_path):
    """Bug real encontrado nesta sessao: dispatch_to_contact nao verificava
    a flag `enabled` da matriz de eventos, diferente de dispatch()."""
    dispatcher, sent = _make_dispatcher(
        tmp_path, events={"VOZ-CHAMAR-CONTATO": EventRuleConfig(enabled=False, notify_contact_ids=[])},
    )

    ok = dispatcher.dispatch_to_contact(make_event(event_id="VOZ-CHAMAR-CONTATO"), "carlos")

    assert ok is False
    time.sleep(0.1)
    assert sent == []


def test_dispatch_to_contact_persists_before_send_like_dispatch(tmp_path):
    dispatcher, sent = _make_dispatcher(tmp_path)

    dispatcher.dispatch_to_contact(make_event(event_id="VOZ-CHAMAR-CONTATO"), "carlos")
    time.sleep(0.3)

    assert dispatcher.queue.list_pending() == {}  # entregue -> removido da fila
