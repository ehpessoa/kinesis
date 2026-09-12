"""Schemas Pydantic de configuração (config.json) e agenda de contatos (contacts.json)."""
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


class CameraSourceConfig(BaseModel):
    """Uma fonte de câmera: webcam local (`index`), RTSP customizado (`rtsp_url`)
    ou RTSP Intelbras montado a partir de `ip`/credenciais. Wi-Fi local e remoto
    via Tailscale usam exatamente este mesmo schema — só o `ip` muda."""

    name: str
    index: Optional[int] = None
    ip: Optional[str] = None
    user: str = "admin"
    password: Optional[str] = None
    port: int = 554
    channel: int = 1
    subtype: int = 1
    rtsp_url: Optional[str] = None

    def resolve_src(self) -> Union[int, str]:
        if self.index is not None:
            return self.index
        if self.rtsp_url:
            return self.rtsp_url
        if self.ip:
            if not self.password:
                raise ValueError(f"Fonte '{self.name}': campo 'password' obrigatorio quando 'ip' e usado.")
            return (
                f"rtsp://{self.user}:{self.password}@{self.ip}:{self.port}"
                f"/cam/realmonitor?channel={self.channel}&subtype={self.subtype}"
            )
        raise ValueError(f"Fonte '{self.name}' invalida: informe 'index', 'rtsp_url' ou 'ip'.")


class EventRuleConfig(BaseModel):
    """Configuração de disparo de UM evento (ex: EVT-01) da matriz de eventos."""

    enabled: bool = True
    severity_override: Optional[str] = None
    notify_contact_ids: List[str] = Field(default_factory=list)


class WhatsAppConfig(BaseModel):
    device_id: str = "SMA-RES-DEFAULT"
    endpoint: Optional[str] = None
    token: Optional[str] = None
    max_retries: int = 4
    backoff_base_seconds: float = 2.0


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


class AppConfig(BaseModel):
    cameras: List[CameraSourceConfig] = Field(
        default_factory=lambda: [CameraSourceConfig(name="Webcam Local", index=0)]
    )
    events: Dict[str, EventRuleConfig] = Field(default_factory=dict)
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    object_detection: ObjectDetectionConfig = Field(default_factory=ObjectDetectionConfig)
    person_tracking: PersonTrackingConfig = Field(default_factory=PersonTrackingConfig)
    pipeline_rates: PipelineRateConfig = Field(default_factory=PipelineRateConfig)

    @model_validator(mode="after")
    def _ensure_at_least_one_camera(self):
        if not self.cameras:
            raise ValueError("config.json precisa declarar ao menos uma camera em 'cameras'.")
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
