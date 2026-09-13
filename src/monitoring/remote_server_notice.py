"""Aviso por WhatsApp de que o servidor remoto (`src/server/remote_server.py`)
acabou de subir — mitiga um gap real de usabilidade: hoje o único jeito de um
familiar/cuidador descobrir o endereço para acessar o dashboard é alguém na
máquina rodar algo como "what's my ip address" e ditar o resultado por
telefone. Em vez disso, ao habilitar `remote_server.enabled` com pelo menos
um contato em `remote_server.notify_contact_ids`, os contatos configurados
recebem uma mensagem única a cada início do processo com data/hora e a URL
pronta para abrir no navegador (já com o token, para não precisar digitá-lo).

Reaproveita o mesmo `NotificationDispatcher.dispatch_to_contact()` usado pelo
check-in programado (`src/monitoring/checkin.py`) e pelo módulo de voz
("chama o <nome>") — herda a fila durável de retry e, se um `EventLogger` for
passado, também aparece no histórico de eventos.

⚠️ A URL enviada inclui o token de acesso na query string (`?token=...`) para
que o link funcione com um único toque. Isso não é uma exposição nova do
segredo: o mesmo token já seria lido por qualquer pessoa com acesso à
conversa de WhatsApp de qualquer forma (ver README, seção "Servidor Remoto").
"""
import socket
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import quote

from config.schemas import AppConfig, ContactsFile
from src.behavior.event_engine import EventNotification

REMOTE_SERVER_STARTED_EVENT_ID = "REMOTE-SERVIDOR-INICIADO"

MESSAGE_TEMPLATE = (
    "Servidor remoto do Kinesis disponivel desde {when}. "
    "Para acompanhar pelo navegador, acesse: {url}"
)


def _detect_outbound_ip() -> Optional[str]:
    """Descobre o IP da interface de rede que a máquina usaria para sair para
    a internet (o `connect` de um socket UDP só escolhe a rota/interface, não
    envia pacote nenhum) — necessário porque `remote_server.host = 0.0.0.0`
    (escutar em todas as interfaces, o valor usado para aceitar tanto Wi-Fi
    local quanto VPN) não é, ele mesmo, um endereço que um cliente consiga
    discar. Retorna `None` se não houver rota de rede disponível.

    ⚠️ Limitação conhecida: numa máquina com VÁRIAS interfaces de rede (ex:
    Wi-Fi local + túnel Tailscale, o caso do Cenário 3 do README), isto só
    reflete a rota PADRÃO de saída — tipicamente a Wi-Fi local, não a VPN —
    porque não há como este processo saber qual interface o contato notificado
    de fato consegue alcançar. Se o objetivo é anunciar o IP da VPN
    especificamente, configure `remote_server.host` com esse IP explícito
    (ver README, seção "Servidor Remoto") em vez de depender desta detecção:
    `resolve_advertised_host` sempre usa um `host` explícito como está, sem
    tentar adivinhar."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(1.0)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def resolve_advertised_host(configured_host: str) -> Optional[str]:
    """`host` explícito (ex: IP do Tailscale, recomendado em README) é usado
    como está. `0.0.0.0`/vazio aciona a detecção automática. `127.0.0.1`
    significa que o acesso remoto foi deliberadamente deixado desligado (ver
    docstring de `RemoteServerConfig`) — não há endereço remoto para anunciar,
    então retorna `None` (o chamador deve pular o aviso nesse caso)."""
    if configured_host in ("127.0.0.1", "localhost"):
        return None
    if configured_host and configured_host != "0.0.0.0":
        return configured_host
    return _detect_outbound_ip()


def build_remote_server_url(host: str, port: int, token: Optional[str]) -> str:
    url = f"http://{host}:{port}/"
    if token:
        url += f"?token={quote(token)}"
    return url


def notify_remote_server_started(
    app_config: AppConfig,
    contacts: ContactsFile,
    dispatcher,
    event_logger=None,
    now: Optional[datetime] = None,
) -> bool:
    """Chamado uma vez, logo após `RemoteStatusServer.start()` (ver
    `main.py`/`gui_main.py`). Não faz nada se `notify_contact_ids` estiver
    vazio (default) ou se não houver um endereço remoto para anunciar (ver
    `resolve_advertised_host`). Retorna True se ao menos um contato foi
    notificado."""
    remote_config = app_config.remote_server
    contact_ids: List[str] = remote_config.notify_contact_ids
    if not contact_ids:
        return False

    host = resolve_advertised_host(remote_config.host)
    if host is None:
        print(
            "remote_server_notice: nao foi possivel determinar um endereco remoto para anunciar "
            f"(host configurado: '{remote_config.host}') - aviso por WhatsApp nao enviado."
        )
        return False

    now = now or datetime.now().astimezone()
    url = build_remote_server_url(host, remote_config.port, remote_config.resolve_token())

    event = EventNotification(
        event_id=REMOTE_SERVER_STARTED_EVENT_ID,
        category="Sistema",
        name="Servidor Remoto Iniciado",
        severity="INFO",
        timestamp=datetime.now(timezone.utc),
        source_name="Sistema",
        message=MESSAGE_TEMPLATE.format(when=now.strftime("%d/%m/%Y %H:%M"), url=url),
    )
    if event_logger is not None:
        event_logger.log(event)

    sent = False
    for contact_id in contact_ids:
        if dispatcher.dispatch_to_contact(event, contact_id, frame_b64=None):
            sent = True
    return sent
