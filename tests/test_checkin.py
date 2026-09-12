"""Testes do check-in programado (src/monitoring/checkin.py). Usa um
dispatcher falso (sem WhatsApp/rede real) para isolar a lógica de "quando
disparar" — o envio de fato já é coberto por test_whatsapp_client.py e
pelo caminho `dispatch_to_contact` em test_storage.py."""
from datetime import datetime

from config.schemas import AppConfig, CheckinConfig, Contact, ContactsFile
from src.monitoring.checkin import CheckinScheduler


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch_to_contact(self, event, contact_id, frame_b64=None):
        self.calls.append((event.event_id, contact_id))
        return True


def make_config(**overrides):
    defaults = dict(enabled=True, times=["08:00", "20:00"], notify_contact_ids=["filho_carlos"])
    defaults.update(overrides)
    return AppConfig(checkins=CheckinConfig(**defaults))


def make_contacts():
    return ContactsFile(contacts=[Contact(id="filho_carlos", name="Carlos", whatsapp_number="5511999998888")])


def test_check_once_sends_at_configured_time():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher)

    sent = scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))

    assert sent is True
    assert dispatcher.calls == [("CHECKIN", "filho_carlos")]


def test_check_once_does_not_send_outside_configured_time():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher)

    sent = scheduler.check_once(now=datetime(2026, 9, 12, 8, 1))

    assert sent is False
    assert dispatcher.calls == []


def test_check_once_does_not_duplicate_within_same_minute():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher)

    first = scheduler.check_once(now=datetime(2026, 9, 12, 8, 0, 5))
    second = scheduler.check_once(now=datetime(2026, 9, 12, 8, 0, 40))

    assert first is True
    assert second is False
    assert len(dispatcher.calls) == 1


def test_check_once_fires_again_next_day():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher)

    scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))
    sent_next_day = scheduler.check_once(now=datetime(2026, 9, 13, 8, 0))

    assert sent_next_day is True
    assert len(dispatcher.calls) == 2


def test_check_once_fires_second_configured_time_same_day():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher)

    scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))
    sent_evening = scheduler.check_once(now=datetime(2026, 9, 12, 20, 0))

    assert sent_evening is True
    assert len(dispatcher.calls) == 2


def test_check_once_disabled_does_nothing():
    dispatcher = FakeDispatcher()
    scheduler = CheckinScheduler(make_config(enabled=False), make_contacts(), dispatcher)

    sent = scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))

    assert sent is False
    assert dispatcher.calls == []


def test_check_once_notifies_every_configured_contact():
    dispatcher = FakeDispatcher()
    config = make_config(notify_contact_ids=["filho_carlos", "cuidadora_ana"])
    contacts = ContactsFile(contacts=[
        Contact(id="filho_carlos", name="Carlos", whatsapp_number="5511999998888"),
        Contact(id="cuidadora_ana", name="Ana", whatsapp_number="5511977776666"),
    ])
    scheduler = CheckinScheduler(config, contacts, dispatcher)

    scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))

    assert dispatcher.calls == [("CHECKIN", "filho_carlos"), ("CHECKIN", "cuidadora_ana")]


def test_check_once_logs_event_when_event_logger_provided(tmp_path):
    from src.storage.event_log import EventLogger

    dispatcher = FakeDispatcher()
    logger = EventLogger(path=str(tmp_path / "events.jsonl"))
    scheduler = CheckinScheduler(make_config(), make_contacts(), dispatcher, event_logger=logger)

    scheduler.check_once(now=datetime(2026, 9, 12, 8, 0))

    logged = logger.read_all()
    assert len(logged) == 1
    assert logged[0]["event_id"] == "CHECKIN"
    assert logged[0]["severity"] == "INFO"
    assert "08:00" in logged[0]["message"]
