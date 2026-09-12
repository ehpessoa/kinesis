"""Check-in programado ("sistema OK") — mitigação do gap de continuidade do
próprio monitoramento (ver README, seção "Continuidade do próprio
monitoramento" do roteiro de evolução): em vez de só alertar quando algo dá
errado, o sistema confirma proativamente que está ativo nos horários
configurados (`config.json -> checkins`). Um check-in que deveria ter
chegado e não chegou vira, por si só, um sinal para quem cuida investigar.

Reaproveita o mesmo `NotificationDispatcher`/`EventNotification` usados
pelos eventos de visão e voz — o check-in é despachado via
`dispatch_to_contact()` (o mesmo método usado pelo módulo de voz para
"chama o <nome>"), então herda a fila durável de retry e, se um
`EventLogger` for passado, também aparece no histórico de eventos.

Isto NÃO substitui uma rotina humana de verificação (visita/ligação) — a
própria mensagem enviada reforça esse lembrete — e NÃO cobre o caso do
processo/máquina cair: se o Kinesis parar de rodar, o check-in também para
de ser enviado. Ver README para a mitigação completa (heartbeat externo,
independente de energia/rede da residência).
"""
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from config.schemas import AppConfig, ContactsFile
from src.behavior.event_engine import EventNotification

CHECKIN_EVENT_ID = "CHECKIN"

CHECKIN_MESSAGE_TEMPLATE = (
    "Sistema ativo e monitorando ({time}). Lembrete: isto e um apoio, "
    "nao substitui visitas e ligacoes regulares."
)


def _parse_hhmm(value: str) -> "tuple[int, int]":
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


class CheckinScheduler:
    """Dispara uma notificação "sistema OK" nos horários configurados
    (`checkins.times`, ex: ["08:00", "14:00", "20:00"]), no máximo uma vez
    por horário por dia. Roda numa thread própria, checando a cada
    `poll_interval_seconds` se algum horário configurado acabou de passar.
    """

    def __init__(
        self,
        config: AppConfig,
        contacts: ContactsFile,
        dispatcher,
        event_logger=None,
        poll_interval_seconds: float = 20.0,
    ):
        self.config = config
        self.contacts = contacts
        self.dispatcher = dispatcher
        self.event_logger = event_logger
        self.poll_interval_seconds = poll_interval_seconds

        self._last_sent_key: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def _due_time_key(self, now: datetime) -> Optional[str]:
        """Retorna uma chave única 'YYYY-MM-DD HH:MM' se `now` bate com
        algum horário configurado (comparando hora e minuto), senão None.
        A chave inclui a data para que o mesmo horário dispare de novo no
        dia seguinte."""
        for hhmm in self.config.checkins.times:
            hour, minute = _parse_hhmm(hhmm)
            if now.hour == hour and now.minute == minute:
                return f"{now.strftime('%Y-%m-%d')} {hhmm}"
        return None

    def check_once(self, now: Optional[datetime] = None) -> bool:
        """Verifica se há um check-in devido agora e, se houver, dispara.
        Retorna True se um check-in foi enviado. Chamado tanto pelo laço
        interno da thread quanto diretamente pelos testes (com um `now`
        controlado, sem depender do relógio real nem de tempo de espera)."""
        if not self.config.checkins.enabled:
            return False
        now = now or datetime.now().astimezone()
        key = self._due_time_key(now)
        if key is None or key == self._last_sent_key:
            return False
        self._last_sent_key = key
        self._send(now)
        return True

    def _send(self, now: datetime) -> None:
        event = EventNotification(
            event_id=CHECKIN_EVENT_ID,
            category="Rotina",
            name="Check-in de Rotina",
            severity="INFO",
            timestamp=datetime.now(timezone.utc),
            source_name="Sistema",
            message=CHECKIN_MESSAGE_TEMPLATE.format(time=now.strftime("%H:%M")),
        )
        if self.event_logger is not None:
            self.event_logger.log(event)
        for contact_id in self.config.checkins.notify_contact_ids:
            self.dispatcher.dispatch_to_contact(event, contact_id, frame_b64=None)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()

        def _loop():
            while not self._stop_event.wait(self.poll_interval_seconds):
                try:
                    self.check_once()
                except Exception as exc:
                    print(f"CheckinScheduler: falha ao enviar check-in programado: {exc}")

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None:
            self._stop_event.set()
            self._thread.join(timeout=2.0)
            self._thread = None
