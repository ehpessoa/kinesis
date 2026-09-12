"""VoiceController de ponta a ponta, com o transcritor/captura mockados
(ver src/audio/transcriber.py: o download do modelo real de ASR depende
de um host — Hugging Face Hub — bloqueado na rede usada para construir
este projeto; não pôde ser validado com um modelo real aqui). O que É
testado de ponta a ponta: resolução de contato falado, roteamento
genérico vs. direto-a-contato, respeito à flag `enabled` da matriz de
eventos, e a thread de orquestração captura->transcrição->intenção->
despacho.
"""
import json
import threading
import time

import httpx
import numpy as np
import pytest

from config.schemas import AppConfig, CameraSourceConfig, Contact, ContactsFile, EventRuleConfig, WhatsAppConfig
from main import NotificationDispatcher
from src.audio.intent_matcher import EmergencyIntentMatcher
from src.audio.voice_controller import VoiceController
from src.notifications.whatsapp_client import WhatsAppNotifier
from src.storage.event_log import EventLogger
from src.storage.notification_queue import PendingNotificationQueue


@pytest.fixture
def wired_controller(tmp_path):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.read()))
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    whatsapp_config = WhatsAppConfig(endpoint="https://zap.example/send", max_retries=1)
    notifier = WhatsAppNotifier(whatsapp_config, client=client)

    app_config = AppConfig(
        cameras=[CameraSourceConfig(name="Sala", index=0)],
        whatsapp=whatsapp_config,
        events={
            "VOZ-SOCORRO": EventRuleConfig(enabled=True, notify_contact_ids=["filho_carlos", "cuidadora_ana"]),
            "VOZ-CHAME-ME": EventRuleConfig(enabled=True, notify_contact_ids=["filho_carlos"]),
            "VOZ-MENSAGEM": EventRuleConfig(enabled=True, notify_contact_ids=["cuidadora_ana"]),
            "VOZ-CHAMAR-CONTATO": EventRuleConfig(enabled=True, notify_contact_ids=[]),
        },
    )
    contacts = ContactsFile(contacts=[
        Contact(id="filho_carlos", name="Carlos", whatsapp_number="5511999998888"),
        Contact(id="cuidadora_ana", name="Ana", whatsapp_number="5511977776666"),
    ])
    dispatcher = NotificationDispatcher(
        app_config, contacts, notifier, queue=PendingNotificationQueue(path=str(tmp_path / "pending.json"))
    )
    event_logger = EventLogger(path=str(tmp_path / "events.jsonl"))

    class _StubTranscriber:
        """Placeholder simples: os testes que exercitam a transcricao de
        verdade substituem `.transcribe` diretamente (nao usamos
        SpeechTranscriber real aqui - o modelo de ASR depende de um host
        bloqueado nesta rede, ver tests/test_transcriber.py)."""
        def transcribe(self, audio):
            return ""

    controller = VoiceController(
        source_name="Sala", audio_source=0, contacts=contacts,
        dispatcher=dispatcher, event_logger=event_logger, transcriber=_StubTranscriber(),
    )
    return controller, sent, app_config, event_logger


def _run_and_wait(controller, intent, sent):
    controller.handle_intent(intent)
    deadline = time.time() + 2.0
    while not sent and time.time() < deadline:
        time.sleep(0.05)


def test_help_broadcasts_to_all_configured_contacts(wired_controller):
    controller, sent, _, _ = wired_controller
    intent = EmergencyIntentMatcher().match("socorro, estou com problemas")

    _run_and_wait(controller, intent, sent)

    assert len(sent) == 2
    assert all(p["event_type"] == "VOZ-SOCORRO" for p in sent)


def test_call_me_broadcasts_to_configured_contact(wired_controller):
    controller, sent, _, _ = wired_controller
    intent = EmergencyIntentMatcher().match("me liga por favor")

    _run_and_wait(controller, intent, sent)

    assert len(sent) == 1
    assert sent[0]["event_type"] == "VOZ-CHAME-ME"
    assert sent[0]["recipient_number"] == "5511999998888"


def test_send_message_without_name_uses_generic_broadcast(wired_controller):
    controller, sent, _, _ = wired_controller
    intent = EmergencyIntentMatcher().match("envia uma mensagem")

    _run_and_wait(controller, intent, sent)

    assert len(sent) == 1
    assert sent[0]["recipient_number"] == "5511977776666"  # cuidadora_ana, via notify_contact_ids


def test_call_contact_routes_directly_bypassing_notify_contact_ids(wired_controller):
    """VOZ-CHAMAR-CONTATO tem notify_contact_ids=[] na config (ver fixture)
    - mesmo assim a notificacao deve chegar ao Carlos, porque o nome foi
    dito explicitamente ("chama o carlos")."""
    controller, sent, _, _ = wired_controller
    intent = EmergencyIntentMatcher().match("chama o carlos")

    _run_and_wait(controller, intent, sent)

    assert len(sent) == 1
    assert sent[0]["recipient_number"] == "5511999998888"


def test_send_message_with_resolved_name_routes_directly(wired_controller):
    controller, sent, _, _ = wired_controller
    intent = EmergencyIntentMatcher().match("manda mensagem para a ana")

    _run_and_wait(controller, intent, sent)

    assert len(sent) == 1
    assert sent[0]["recipient_number"] == "5511977776666"


def test_call_contact_with_unresolved_name_sends_nothing(wired_controller):
    controller, sent, _, event_logger = wired_controller
    intent = EmergencyIntentMatcher().match("chama o zezinho")

    controller.handle_intent(intent)
    time.sleep(0.3)

    assert sent == []
    logged = event_logger.read_all()
    assert len(logged) == 1  # o evento ainda e logado, mesmo sem notificacao enviada
    assert logged[0]["event_id"] == "VOZ-CHAMAR-CONTATO"


def test_disabled_event_rule_blocks_direct_contact_dispatch(wired_controller):
    """dispatch_to_contact deve respeitar a flag `enabled`, nao so
    dispatch() - bug real encontrado nesta sessao."""
    controller, sent, app_config, _ = wired_controller
    app_config.events["VOZ-CHAMAR-CONTATO"].enabled = False
    intent = EmergencyIntentMatcher().match("chama o carlos")

    controller.handle_intent(intent)
    time.sleep(0.3)

    assert sent == []


def test_all_matched_intents_are_logged_even_when_not_sent(wired_controller):
    controller, sent, _, event_logger = wired_controller
    matcher = EmergencyIntentMatcher()
    for text in ["socorro", "me liga", "envia uma mensagem", "chama o inexistente"]:
        controller.handle_intent(matcher.match(text))
    time.sleep(0.3)

    assert len(event_logger.read_all()) == 4


def test_voice_loop_dispatches_only_on_matching_chunk(wired_controller):
    """Testa a thread de orquestracao real (_loop): captura e transcricao
    mockadas, mas o fluxo captura->transcricao->match->despacho e o
    codigo real do VoiceController."""
    controller, sent, _, _ = wired_controller

    fake_chunks = [np.zeros(10, dtype=np.float32)] * 3 + [None]
    chunk_cycle = iter(fake_chunks * 5)
    controller.capture.get_chunk = lambda timeout=1.0: next(chunk_cycle, None)

    texts = iter(["tudo bem por aqui", "socorro, preciso de ajuda", "so testando"])
    controller.transcriber.transcribe = lambda audio: next(texts, "")

    controller._running = True
    thread = threading.Thread(target=controller._loop, daemon=True)
    thread.start()
    time.sleep(1.0)
    controller._running = False
    thread.join(timeout=2.0)

    # VOZ-SOCORRO esta configurado na fixture para 2 contatos (filho_carlos
    # + cuidadora_ana) - o que importa aqui e que so o chunk com "socorro"
    # disparou algo, os outros dois (sem palavra-chave) nao geraram envio.
    assert len(sent) == 2
    assert all(p["event_type"] == "VOZ-SOCORRO" for p in sent)
