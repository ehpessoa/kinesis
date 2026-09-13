"""Schemas Pydantic de configuração (config.json) e agenda de contatos (contacts.json)."""
import os
from typing import ClassVar, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_time_of_day(value: str) -> str:
    """Valida uma string HH:MM (00-23 / 00-59) — compartilhado por todo
    horário de configuração do projeto (check-ins, janela noturna do
    EVT-10) para não duplicar a mesma checagem em cada schema."""
    parts = value.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Horario invalido: '{value}' (use o formato HH:MM).")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Horario invalido: '{value}' (use o formato HH:MM).")
    return value


class CameraSourceConfig(BaseModel):
    """Uma fonte de câmera: webcam local (`index`), RTSP totalmente customizado
    (`rtsp_url`) ou RTSP montado a partir de `ip`/credenciais + um template de
    path (`rtsp_path_template`). Wi-Fi local e remoto via Tailscale usam
    exatamente este mesmo schema — só o `ip` muda.

    A aplicação em si é **agnóstica de fabricante**: nada no código assume uma
    marca específica de câmera. `rtsp_path_template` é o que determina o
    protocolo, e tem como default o path usado por câmeras Dahua e OEMs da
    mesma plataforma (Intelbras é a mais comum no Brasil) — a única marca
    citada nos exemplos deste projeto por ser a testada, não por exigência de
    código. Para outro fabricante/protocolo, troque só o template, ex:

        "rtsp_path_template": "/Streaming/Channels/{channel}01"   # Hikvision

    Os placeholders disponíveis são `{channel}` e `{subtype}` (valores dos
    campos abaixo); a URL final é
    `rtsp://<user>:<senha>@<ip>:<port><rtsp_path_template>`. Câmeras cujo
    protocolo não caiba nesse formato (query string totalmente diferente,
    porta não-RTSP, etc.) devem usar `rtsp_url` com a URL completa.

    A senha RTSP é um segredo tão sensível quanto a apikey do WhatsApp, mas
    precisa ser resolvida por câmera (a lista `cameras` pode ter várias). Por
    isso `password_env` indica o NOME de uma variável de ambiente (definida
    no `.env`, ver `.env.example`) de onde a senha real é lida em tempo de
    uso — `config.json` guarda só o nome da variável, nunca o valor. O campo
    `password` continua aceito por compatibilidade com instalações antigas,
    mas fica em texto puro no arquivo; prefira `password_env` em config novas."""

    DEFAULT_RTSP_PATH_TEMPLATE: ClassVar[str] = "/cam/realmonitor?channel={channel}&subtype={subtype}"

    name: str
    index: Optional[int] = None
    ip: Optional[str] = None
    user: str = "admin"
    password: Optional[str] = None
    password_env: Optional[str] = None
    port: int = 554
    channel: int = 1
    subtype: int = 1
    rtsp_path_template: str = DEFAULT_RTSP_PATH_TEMPLATE
    rtsp_url: Optional[str] = None

    def resolve_password(self) -> Optional[str]:
        if self.password_env:
            return os.environ.get(self.password_env)
        return self.password

    def resolve_src(self) -> Union[int, str]:
        if self.index is not None:
            return self.index
        if self.rtsp_url:
            return self.rtsp_url
        if self.ip:
            password = self.resolve_password()
            if not password:
                if self.password_env:
                    raise ValueError(
                        f"Fonte '{self.name}': variavel de ambiente '{self.password_env}' "
                        "(password_env) nao definida ou vazia."
                    )
                raise ValueError(
                    f"Fonte '{self.name}': defina 'password_env' (recomendado) ou 'password' "
                    "quando 'ip' e usado."
                )
            try:
                path = self.rtsp_path_template.format(channel=self.channel, subtype=self.subtype)
            except (KeyError, IndexError) as exc:
                raise ValueError(
                    f"Fonte '{self.name}': 'rtsp_path_template' invalido ({exc}); "
                    "use somente os placeholders {channel} e {subtype}, ou use 'rtsp_url' "
                    "para uma URL completa customizada."
                ) from exc
            return f"rtsp://{self.user}:{password}@{self.ip}:{self.port}{path}"
        raise ValueError(f"Fonte '{self.name}' invalida: informe 'index', 'rtsp_url' ou 'ip'.")


class EventRuleConfig(BaseModel):
    """Configuração de disparo de UM evento (ex: EVT-01) da matriz de eventos."""

    enabled: bool = True
    severity_override: Optional[str] = None
    notify_contact_ids: List[str] = Field(default_factory=list)


class WhatsAppConfig(BaseModel):
    """Configuração do gateway WhatsApp (Evolution API). A apikey NUNCA fica
    aqui (nem em config.json) — vem exclusivamente da variável de ambiente
    EVOLUTION_API_KEY (ver .env.example e src/notifications/whatsapp_client.py),
    para não expor o segredo num arquivo que a GUI le/escreve em texto puro.

    `endpoint`/`instance` não são segredo (é a URL/nome pública da instância),
    então continuam com um valor default aceitável em config.json — mas
    `KINESIS_WHATSAPP_ENDPOINT`/`KINESIS_WHATSAPP_INSTANCE`, quando definidas,
    têm prioridade (ver `resolve_endpoint`/`resolve_instance`), útil para
    trocar de instância entre ambientes (dev/prod) sem editar config.json."""

    ENDPOINT_ENV_VAR: ClassVar[str] = "KINESIS_WHATSAPP_ENDPOINT"
    INSTANCE_ENV_VAR: ClassVar[str] = "KINESIS_WHATSAPP_INSTANCE"

    endpoint: Optional[str] = None  # Base URL da Evolution API, ex: https://message.senszia.com
    instance: Optional[str] = None  # Nome da instancia Evolution, ex: senszia
    max_retries: int = 4
    backoff_base_seconds: float = 2.0

    def resolve_endpoint(self) -> Optional[str]:
        return os.environ.get(self.ENDPOINT_ENV_VAR) or self.endpoint

    def resolve_instance(self) -> Optional[str]:
        return os.environ.get(self.INSTANCE_ENV_VAR) or self.instance


class ObjectDetectionConfig(BaseModel):
    """Detecção de dispositivos de mobilidade assistiva (bengala, andador,
    cadeira de rodas) via YOLO-World. Desabilitada por padrão: traz uma
    dependência pesada (ultralytics/torch, ~600MB de pesos na 1a execução)
    que nem todo dispositivo de borda deve pagar.

    `confidence` default é conservador (0.35): em teste neste projeto, ruído
    puro gerou falsos positivos com confiança de até ~0.31 (ver README) — um
    limiar baixo deixa a leitura do HUD pouco confiável. Calibre com imagens
    reais do ambiente de instalação antes de usar o valor em produção."""

    enabled: bool = False
    confidence: float = 0.35
    frame_interval: int = 5  # roda a deteccao a cada N frames (mitiga custo de CPU)


class PersonTrackingConfig(BaseModel):
    """Rastreamento contínuo de pessoas (ByteTrack) — mitigação do item
    4.1.3 do plano: substitui a reidentificação facial biométrica repetida
    (pouco confiável em câmeras distantes/ângulos inclinados) por um ID de
    rastreamento contínuo desde a entrada da pessoa no ambiente.

    Habilitado por padrão: usa YOLOv8n padrão (classe "person" do COCO-80,
    ~6MB), bem mais leve que o YOLO-World de object_detection. `frame_interval`
    é 1 (todo frame) por padrão — reduzir prejudica a continuidade que o
    ByteTrack depende para associar detecções entre frames."""

    enabled: bool = True
    confidence: float = 0.4
    frame_interval: int = 1


class PipelineRateConfig(BaseModel):
    """Otimização de taxa de quadros por sub-pipeline — mitigação do item
    4.1.1 do plano ("Processamento Multimodal Completo Simultâneo em
    Hardware Básico"): rodar Pose, Face e Gesture a cada frame da câmera
    sobrecarrega CPU sem GPU dedicada. O plano recomenda Pose a 15-20 FPS e
    Face/Blendshapes a 5-10 FPS; aplicamos o mesmo raciocínio ao Gesture
    (não especificado no plano, mas tão custoso quanto Face).

    O throttle é por tempo de parede (Hz), não por contagem de frames — ver
    src/vision/rate_limiter.py — para valer o mesmo ritmo tanto na webcam
    (~30 FPS) quanto em RTSP (taxa variável). Entre execuções, o último
    resultado de cada detector é reaproveitado (frame "congelado")."""

    pose_fps: float = 18.0
    face_fps: float = 8.0
    gesture_fps: float = 8.0


class VoiceConfig(BaseModel):
    """Identificação de comandos de emergência por voz ("me liga", "estou
    com problemas", "envia uma mensagem", "chama o <nome>" — ver
    src/audio/intent_matcher.py) a partir do áudio da câmera (RTSP) ou,
    para webcam local, do microfone padrão do sistema (ver
    src/audio/camera_audio_capture.py).

    Desabilitado por padrão: depende de `faster-whisper` (ASR) e, por
    padrão, baixa um modelo do Hugging Face Hub na primeira execução — um
    host que pode estar bloqueado em alguns ambientes de rede restrita
    (foi o caso no ambiente usado para construir este projeto). Se for o
    seu caso, baixe o modelo manualmente em uma máquina com acesso e
    aponte `model_dir` para a pasta convertida (ver README).

    Os comandos genéricos ("me liga", "socorro"/"estou com problemas",
    "envia uma mensagem" sem nome) são roteados pela MESMA matriz `events`
    usada pelos eventos de visão (chaves VOZ-CHAME-ME, VOZ-SOCORRO,
    VOZ-MENSAGEM, VOZ-CHAMAR-CONTATO — ver config.example.json), e não por
    um campo de destinatários próprio.

    `model_dir` é um caminho de filesystem, portanto varia por máquina (ex:
    onde o modelo foi baixado manualmente numa instalação sem acesso ao
    Hugging Face Hub) — `KINESIS_VOICE_MODEL_DIR`, quando definida, tem
    prioridade sobre o campo (ver `resolve_model_dir`), para não precisar
    editar config.json ao mover a instalação entre máquinas."""

    MODEL_DIR_ENV_VAR: ClassVar[str] = "KINESIS_VOICE_MODEL_DIR"

    enabled: bool = False
    language: str = "pt"
    model_size: str = "small"
    model_dir: Optional[str] = None
    chunk_seconds: float = 4.0

    def resolve_model_dir(self) -> Optional[str]:
        return os.environ.get(self.MODEL_DIR_ENV_VAR) or self.model_dir


class CheckinConfig(BaseModel):
    """Check-in programado ("sistema OK") — mitigação do gap de continuidade
    do próprio monitoramento identificado no roteiro de evolução: em vez de
    só alertar quando algo dá errado, o sistema confirma proativamente que
    está ativo nos horários configurados. Silêncio inesperado (o check-in
    que deveria ter chegado e não chegou) vira, por si só, um sinal para
    quem cuida notar.

    Isto NÃO substitui uma rotina humana de verificação (visita/ligação) —
    a própria mensagem enviada reforça esse lembrete — e NÃO cobre o caso
    do processo/máquina cair: se o Kinesis parar de rodar, o check-in
    também para de ser enviado (mesma limitação de qualquer notificação
    que depende do próprio processo estar de pé). Um heartbeat
    verdadeiramente independente de falha exige um segundo dispositivo com
    bateria/rede próprios — fora do escopo desta etapa (ver README)."""

    enabled: bool = False
    times: List[str] = Field(default_factory=lambda: ["08:00", "14:00", "20:00"])
    notify_contact_ids: List[str] = Field(default_factory=list)

    @field_validator("times")
    @classmethod
    def _validate_times(cls, value: List[str]) -> List[str]:
        for item in value:
            _validate_time_of_day(item)
        return value


class RemoteServerConfig(BaseModel):
    """Servidor HTTP local somente-leitura (`src/server/remote_server.py`)
    para um familiar/cuidador remoto acompanhar status, histórico e um
    snapshot da câmera pelo celular — sem precisar abrir o desktop PyQt6 na
    máquina instalada. Mitiga o gap "único jeito de ver o sistema é a
    máquina instalada" do roteiro de evolução.

    NÃO é um servidor pensado para a internet pública: o único controle de
    acesso é um token estático, adequado para uma rede já autenticada por
    VPN (ex: Tailscale — o mesmo túnel já usado para a câmera remota, ver
    README), não para exposição direta na internet. `host` deve apontar
    para a interface da VPN (ou ficar em 127.0.0.1 se o acesso remoto ainda
    não for necessário); nunca faça port-forward desta porta no roteador.

    O token é um segredo (quem o possui acessa histórico e snapshots de
    câmera remotamente), então o caminho recomendado é defini-lo só via
    `KINESIS_REMOTE_SERVER_TOKEN` (ver .env.example) — `resolve_token()` lê
    essa variável com prioridade sobre o campo `token`, que continua aceito
    em config.json por compatibilidade com instalações antigas."""

    TOKEN_ENV_VAR: ClassVar[str] = "KINESIS_REMOTE_SERVER_TOKEN"

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    token: Optional[str] = None
    snapshot_fps: float = 0.5

    def resolve_token(self) -> Optional[str]:
        return os.environ.get(self.TOKEN_ENV_VAR) or self.token

    @model_validator(mode="after")
    def _require_token_when_enabled(self):
        if self.enabled and not self.resolve_token():
            raise ValueError(
                "remote_server.token (ou a variavel de ambiente KINESIS_REMOTE_SERVER_TOKEN) "
                "e obrigatorio quando remote_server.enabled=true "
                "(e o unico controle de acesso ao servidor - nunca habilite sem um token)."
            )
        return self


class NightRoutineConfig(BaseModel):
    """EVT-10 (Ausência da Cama no Horário Noturno) — mitigação do item da
    matriz assistencial que dependia de duas capacidades que não existiam
    antes desta etapa: uma zona espacial configurável (a cama) e uma janela
    horária. Implementadas aqui como o mínimo necessário para o evento
    funcionar, não como um editor visual completo (ver ressalva abaixo).

    `bed_zone` é um polígono em coordenadas NORMALIZADAS (0.0-1.0, relativas
    à largura/altura do frame) — assim a mesma configuração vale mesmo que a
    resolução da câmera mude. Para definir os pontos: pause um frame de
    referência (ex: um print da GUI), meça a posição de cada canto da cama
    em pixels e divida pela largura/altura da imagem. Não há (ainda) uma
    ferramenta de desenho de zona na GUI — é uma configuração manual em JSON,
    ver README.

    `night_start`/`night_end` podem cruzar a meia-noite (ex: "22:00"/"06:00")
    — o motor de eventos trata isso corretamente. Fora dessa janela, EVT-10
    nunca dispara, mesmo com a pessoa fora da zona da cama.

    Depende de `person_tracking.enabled=true`: sem o rastreamento de pessoa
    (ByteTrack) não há como saber onde ela está em relação à zona da cama —
    o schema recusa `night_routine.enabled=true` sem isso (ver AppConfig)."""

    enabled: bool = False
    bed_zone: List[Tuple[float, float]] = Field(default_factory=list)
    night_start: str = "22:00"
    night_end: str = "06:00"
    absence_threshold_minutes: float = 20.0

    @field_validator("night_start", "night_end")
    @classmethod
    def _validate_times(cls, value: str) -> str:
        return _validate_time_of_day(value)

    @model_validator(mode="after")
    def _require_bed_zone_when_enabled(self):
        if self.enabled and len(self.bed_zone) < 3:
            raise ValueError(
                "night_routine.bed_zone precisa de pelo menos 3 pontos (poligono) "
                "quando night_routine.enabled=true."
            )
        return self


class SeizureDetectionConfig(BaseModel):
    """EVT-11 (Detecção de Convulsão/Tremores) — estima a frequência
    dominante de oscilação do pulso via FFT (`BehaviorTracker._analyze_tremor`)
    numa janela recente, e dispara quando ela cai numa faixa típica de
    movimento clônico/tremor (`freq_min_hz`-`freq_max_hz`) com amplitude
    mínima (`min_amplitude`, normalizada pela altura do tronco — invariante
    à distância da câmera) sustentada por `hold_seconds`.

    ⚠️ **Limiares não calibrados contra convulsões reais** — mesma limitação
    já documentada para `object_detection.confidence`: este projeto não tem
    acesso a filmagem real de uma crise convulsiva para calibrar
    `min_amplitude`. Os padrões são uma estimativa de engenharia a partir da
    faixa de frequência de movimento clônico citada na literatura geral, não
    uma calibração clínica. **Desabilitado por padrão** por isso — para
    EVT-11 (severidade Crítica), tanto o falso-negativo quanto o
    falso-positivo recorrente (fadiga de alerta) são riscos reais.

    `freq_max_hz` tem que ficar folgadamente abaixo da metade de
    `pipeline_rates.pose_fps` (limite de Nyquist da FFT) — com o padrão
    `pose_fps=18`, o limite é 9Hz; `freq_max_hz=6.0` mantém margem. Reduzir
    `pose_fps` sem reduzir `freq_max_hz` na mesma proporção faz a estimativa
    de frequência perder confiabilidade."""

    enabled: bool = False
    freq_min_hz: float = 2.0
    freq_max_hz: float = 6.0
    min_amplitude: float = 0.05
    hold_seconds: float = 3.0


class StorageConfig(BaseModel):
    """Retenção e expurgo automático do histórico de eventos persistido em
    SQLite (`data/events.db`, ver `src/storage/event_log.py`). Sem isso, o
    histórico cresce indefinidamente numa instalação de longa duração,
    acumulando dados sensíveis (frames de câmera anexados a alertas,
    rótulos de pessoa) sem prazo — `retention_hours` é também o parâmetro
    citado no roteiro de evolução como mitigação de privacidade/LGPD."""

    retention_hours: float = 24.0
    purge_interval_minutes: float = 60.0


class AppConfig(BaseModel):
    cameras: List[CameraSourceConfig] = Field(
        default_factory=lambda: [CameraSourceConfig(name="Webcam Local", index=0)]
    )
    events: Dict[str, EventRuleConfig] = Field(default_factory=dict)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    object_detection: ObjectDetectionConfig = Field(default_factory=ObjectDetectionConfig)
    person_tracking: PersonTrackingConfig = Field(default_factory=PersonTrackingConfig)
    pipeline_rates: PipelineRateConfig = Field(default_factory=PipelineRateConfig)
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    checkins: CheckinConfig = Field(default_factory=CheckinConfig)
    remote_server: RemoteServerConfig = Field(default_factory=RemoteServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    night_routine: NightRoutineConfig = Field(default_factory=NightRoutineConfig)
    seizure_detection: SeizureDetectionConfig = Field(default_factory=SeizureDetectionConfig)

    @model_validator(mode="after")
    def _ensure_at_least_one_camera(self):
        if not self.cameras:
            raise ValueError("config.json precisa declarar ao menos uma camera em 'cameras'.")
        return self

    @model_validator(mode="after")
    def _night_routine_requires_person_tracking(self):
        if self.night_routine.enabled and not self.person_tracking.enabled:
            raise ValueError(
                "night_routine.enabled=true exige person_tracking.enabled=true "
                "(EVT-10 precisa saber onde a pessoa esta em relacao a zona da cama)."
            )
        return self


class Contact(BaseModel):
    id: str
    name: str
    relationship: Optional[str] = None
    whatsapp_number: Optional[str] = None


class ContactsFile(BaseModel):
    contacts: List[Contact] = Field(default_factory=list)

    def find(self, contact_id: str) -> Optional[Contact]:
        return next((c for c in self.contacts if c.id == contact_id), None)
