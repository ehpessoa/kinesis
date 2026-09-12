"""Motor de Regras e Eventos (Matriz de Monitoramento).

Implementa 10 dos 12 eventos da matriz assistencial (EVT-01, 02, 03, 04, 06,
07, 08, 09, 10, 11), deriváveis das métricas que o BehaviorTracker já calcula
por frame mais, para EVT-10, a posição da pessoa rastreada (`PersonTracker`).
Os 2 eventos restantes ainda exigem capacidades que este projeto não tem e
por isso NÃO estão implementados aqui (evitando um "meio pronto" que pareça
funcionar sem funcionar de verdade):

  - EVT-05 (Desorientação/Confusão): precisa de um classificador de padrão de
    olhar/cabeça errático sustentado por ~60s — ainda não modelado.
  - EVT-12 (Zona de risco): a mesma checagem ponto-em-polígono usada por
    EVT-10 abaixo já resolve a parte geométrica; falta o conceito de
    múltiplas zonas nomeadas (escada, fogão) na configuração e na GUI.

EVT-10 (Ausência da Cama no Horário Noturno) e EVT-11 (Convulsão/Tremores)
foram implementados nesta etapa:

  - EVT-10 usa uma "zona da cama" poligonal configurável (coordenadas
    normalizadas) e uma janela horária — ver `config.schemas.NightRoutineConfig`.
    Depende de `person_tracking.enabled=true` para saber onde a pessoa está.
  - EVT-11 usa análise em frequência (FFT) da oscilação do pulso — ver
    `BehaviorTracker._analyze_tremor` e `config.schemas.SeizureDetectionConfig`.
    ⚠️ Limiares não calibrados contra convulsões reais (mesma ressalva já
    documentada para `object_detection.confidence`) — desabilitado por
    padrão, ver README antes de habilitar em produção.

Cada instância de EventEngine deve ser dedicada a UMA fonte de câmera — o
estado de debounce (timers de "desde quando") não pode ser compartilhado
entre pessoas/câmeras diferentes.
"""
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel

from config.schemas import NightRoutineConfig, SeizureDetectionConfig

EVENT_CATALOG: Dict[str, Dict[str, str]] = {
    "EVT-01": {"category": "Queda", "name": "Queda Brusca Detectada", "default_severity": "CRITICAL"},
    "EVT-02": {"category": "Saude", "name": "Imobilidade Prolongada pos-queda", "default_severity": "CRITICAL"},
    "EVT-03": {"category": "Saude", "name": "Inatividade Geral Excessiva", "default_severity": "MEDIUM"},
    "EVT-04": {"category": "Atencao", "name": "Sonolencia / Olhos Fechados", "default_severity": "MEDIUM"},
    "EVT-06": {"category": "Gestos", "name": "Gesto de SOS / Pedido de Ajuda", "default_severity": "HIGH"},
    "EVT-07": {"category": "Gestos", "name": "Mao no Rosto / Mal-Estar", "default_severity": "MEDIUM"},
    "EVT-08": {"category": "Postura", "name": "Mudanca de Postura", "default_severity": "INFO"},
    "EVT-09": {"category": "Emocao", "name": "Expressao de Dor / Distress", "default_severity": "MEDIUM"},
    "EVT-10": {"category": "Rotina", "name": "Ausencia da Cama no Horario Noturno", "default_severity": "HIGH"},
    "EVT-11": {"category": "Saude", "name": "Deteccao de Convulsao / Tremores", "default_severity": "CRITICAL"},
}


def _point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray casting padrão (paridade de cruzamentos com uma semi-reta
    horizontal a partir do ponto). `polygon` e o ponto testado devem estar
    na mesma escala — aqui, coordenadas normalizadas 0.0-1.0."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def _is_within_night_window(now_dt: datetime, night_start: str, night_end: str) -> bool:
    """Suporta janelas que cruzam a meia-noite (ex: 22:00 -> 06:00)."""
    start_h, start_m = (int(p) for p in night_start.split(":"))
    end_h, end_m = (int(p) for p in night_end.split(":"))
    start_minutes = start_h * 60 + start_m
    end_minutes = end_h * 60 + end_m
    current_minutes = now_dt.hour * 60 + now_dt.minute

    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


class EventNotification(BaseModel):
    event_id: str
    category: str
    name: str
    severity: str
    timestamp: datetime
    source_name: str
    message: str
    frame_base64: Optional[str] = None
    person_label: Optional[str] = None


def _build_event(
    event_id: str, source_name: str, message: str,
    severity_override: Optional[str] = None, person_label: Optional[str] = None,
) -> EventNotification:
    meta = EVENT_CATALOG[event_id]
    return EventNotification(
        event_id=event_id,
        category=meta["category"],
        name=meta["name"],
        severity=severity_override or meta["default_severity"],
        timestamp=datetime.now(timezone.utc),
        source_name=source_name,
        message=message,
        person_label=person_label,
    )


class EventEngine:
    def __init__(
        self,
        source_name: str,
        immobility_after_fall_seconds: float = 30.0,
        inactivity_threshold_seconds: float = 2 * 60 * 60,
        sos_hold_seconds: float = 3.0,
        hand_face_hold_seconds: float = 5.0,
        distress_threshold: float = 0.45,
        distress_hold_seconds: float = 2.0,
        night_routine_config: Optional[NightRoutineConfig] = None,
        seizure_config: Optional[SeizureDetectionConfig] = None,
    ):
        self.source_name = source_name
        self.immobility_after_fall_seconds = immobility_after_fall_seconds
        self.inactivity_threshold_seconds = inactivity_threshold_seconds
        self.sos_hold_seconds = sos_hold_seconds
        self.hand_face_hold_seconds = hand_face_hold_seconds
        self.distress_threshold = distress_threshold
        self.distress_hold_seconds = distress_hold_seconds
        self.night_routine_config = night_routine_config or NightRoutineConfig()
        self.seizure_config = seizure_config or SeizureDetectionConfig()

        # EVT-10: timer de "desde quando fora da zona da cama" durante a
        # janela noturna configurada.
        self._bed_absence_since: Optional[float] = None
        self._bed_absence_notified = False

        # EVT-11: timer de "desde quando a frequencia dominante do pulso
        # esta na faixa de tremor configurada".
        self._tremor_since: Optional[float] = None
        self._seizure_notified = False

        self._prev_fall_alert = False
        self._fall_episode_started_at: Optional[float] = None
        self._fall_episode_broken = False

        # Inicializado no primeiro update(), com o `now` recebido - nao aqui
        # com time.time() real, para que o motor funcione corretamente
        # tambem quando alimentado com timestamps simulados (testes,
        # eventual replay de video gravado).
        self._last_activity_at: Optional[float] = None
        self._inactivity_notified = False

        self._prev_drowsy_alert = False

        self._arm_raised_since: Optional[float] = None
        self._sos_notified = False

        self._hand_face_since: Optional[float] = None
        self._hand_face_notified = False

        self._prev_posture: Optional[str] = None

        self._distress_since: Optional[float] = None
        self._distress_notified = False

    def update(
        self, pose_data: dict, blend_data: dict, now: Optional[float] = None,
        person_label: Optional[str] = None, person_point: Optional[Tuple[float, float]] = None,
    ) -> List[EventNotification]:
        """`person_point`: posição normalizada (0.0-1.0) da pessoa "principal"
        no frame (ex: base da caixa delimitadora do `PersonTracker`), usada
        só pelo EVT-10. `None` quando `person_tracking` está desabilitado ou
        nenhuma pessoa foi detectada no frame atual."""
        now = now if now is not None else time.time()
        events: List[EventNotification] = []

        fall_alert = pose_data.get("fall_alert", False)
        dynamic_state = pose_data.get("dynamic_state", "Estatico")
        posture = pose_data.get("posture", "N/A")
        arm_raised = pose_data.get("arm_raised", False)
        hand_near_face = pose_data.get("hand_near_face", False)
        drowsy_alert = blend_data.get("drowsy_alert", False)
        distress_score = blend_data.get("distress_score", 0.0)

        # EVT-01: borda de subida do alerta de queda.
        if fall_alert and not self._prev_fall_alert:
            events.append(_build_event("EVT-01", self.source_name, "Queda brusca detectada.", person_label=person_label))
            self._fall_episode_started_at = now
            self._fall_episode_broken = False
        self._prev_fall_alert = fall_alert

        # EVT-02: imobilidade sustentada apos uma queda, ja com o alerta
        # visual de EVT-01 encerrado (que dura so 3s no BehaviorTracker).
        if self._fall_episode_started_at is not None:
            if dynamic_state != "Estatico":
                self._fall_episode_broken = True
            elapsed = now - self._fall_episode_started_at
            if elapsed >= self.immobility_after_fall_seconds:
                if not self._fall_episode_broken:
                    events.append(_build_event(
                        "EVT-02", self.source_name,
                        f"Imobilidade por mais de {int(self.immobility_after_fall_seconds)}s apos queda.",
                        person_label=person_label,
                    ))
                self._fall_episode_started_at = None

        # EVT-03: inatividade geral. Qualquer movimento ou mudanca de postura
        # conta como atividade e reinicia a janela.
        if self._last_activity_at is None:
            self._last_activity_at = now
        is_active = dynamic_state != "Estatico" or (self._prev_posture is not None and posture != self._prev_posture)
        if is_active:
            self._last_activity_at = now
            self._inactivity_notified = False
        elif not self._inactivity_notified and (now - self._last_activity_at) >= self.inactivity_threshold_seconds:
            events.append(_build_event(
                "EVT-03", self.source_name,
                f"Sem atividade detectada por mais de {int(self.inactivity_threshold_seconds // 60)} min.",
                person_label=person_label,
            ))
            self._inactivity_notified = True

        # EVT-04: borda de subida do alerta de sonolencia.
        if drowsy_alert and not self._prev_drowsy_alert:
            events.append(_build_event(
                "EVT-04", self.source_name, "Sinal de sonolencia (olhos fechados prolongado).",
                person_label=person_label,
            ))
        self._prev_drowsy_alert = drowsy_alert

        # EVT-06: braco levantado sustentado (gesto de SOS).
        if arm_raised:
            if self._arm_raised_since is None:
                self._arm_raised_since = now
            elif not self._sos_notified and (now - self._arm_raised_since) >= self.sos_hold_seconds:
                events.append(_build_event(
                    "EVT-06", self.source_name,
                    f"Braco levantado por mais de {self.sos_hold_seconds:.0f}s (possivel pedido de ajuda).",
                    person_label=person_label,
                ))
                self._sos_notified = True
        else:
            self._arm_raised_since = None
            self._sos_notified = False

        # EVT-07: mao proxima ao rosto sustentada (mal-estar/tontura).
        if hand_near_face:
            if self._hand_face_since is None:
                self._hand_face_since = now
            elif not self._hand_face_notified and (now - self._hand_face_since) >= self.hand_face_hold_seconds:
                events.append(_build_event(
                    "EVT-07", self.source_name,
                    f"Mao proxima ao rosto por mais de {self.hand_face_hold_seconds:.0f}s.",
                    person_label=person_label,
                ))
                self._hand_face_notified = True
        else:
            self._hand_face_since = None
            self._hand_face_notified = False

        # EVT-08: mudanca de postura (informativo, sem debounce por duracao).
        if self._prev_posture is not None and posture != self._prev_posture and posture != "N/A":
            events.append(_build_event(
                "EVT-08", self.source_name, f"Mudanca de postura: {self._prev_posture} -> {posture}.",
                person_label=person_label,
            ))
        if posture != "N/A":
            self._prev_posture = posture

        # EVT-09: expressao de dor/distress sustentada.
        if distress_score >= self.distress_threshold:
            if self._distress_since is None:
                self._distress_since = now
            elif not self._distress_notified and (now - self._distress_since) >= self.distress_hold_seconds:
                events.append(_build_event(
                    "EVT-09", self.source_name, "Expressao facial de dor/distress sustentada.",
                    person_label=person_label,
                ))
                self._distress_notified = True
        else:
            self._distress_since = None
            self._distress_notified = False

        # EVT-10: ausencia sustentada da zona da cama durante a janela
        # noturna configurada. "Sem pessoa detectada" conta como ausencia
        # (nao como "sem dado, ignorar") - e o cenario mais obvio de
        # deambulacao (a pessoa saiu do comodo/campo de visao), e nunca
        # deixar "falta de dado" significar silenciosamente "seguro" e a
        # escolha certa para um evento de seguranca.
        if self.night_routine_config.enabled:
            now_dt = datetime.fromtimestamp(now)
            if _is_within_night_window(now_dt, self.night_routine_config.night_start, self.night_routine_config.night_end):
                in_bed = person_point is not None and _point_in_polygon(
                    person_point[0], person_point[1], self.night_routine_config.bed_zone,
                )
                if in_bed:
                    self._bed_absence_since = None
                    self._bed_absence_notified = False
                else:
                    if self._bed_absence_since is None:
                        self._bed_absence_since = now
                    elif not self._bed_absence_notified and (
                        now - self._bed_absence_since
                    ) >= self.night_routine_config.absence_threshold_minutes * 60:
                        events.append(_build_event(
                            "EVT-10", self.source_name,
                            f"Ausente da zona da cama por mais de "
                            f"{int(self.night_routine_config.absence_threshold_minutes)} min durante o horario noturno.",
                            person_label=person_label,
                        ))
                        self._bed_absence_notified = True
            else:
                self._bed_absence_since = None
                self._bed_absence_notified = False

        # EVT-11: frequencia dominante de oscilacao do pulso (calculada por
        # BehaviorTracker._analyze_tremor) sustentada dentro da faixa
        # configurada, com amplitude minima.
        if self.seizure_config.enabled:
            tremor_hz = pose_data.get("tremor_hz")
            tremor_amplitude = pose_data.get("tremor_amplitude", 0.0)
            in_tremor_band = (
                tremor_hz is not None
                and self.seizure_config.freq_min_hz <= tremor_hz <= self.seizure_config.freq_max_hz
                and tremor_amplitude >= self.seizure_config.min_amplitude
            )
            if in_tremor_band:
                if self._tremor_since is None:
                    self._tremor_since = now
                elif not self._seizure_notified and (now - self._tremor_since) >= self.seizure_config.hold_seconds:
                    events.append(_build_event(
                        "EVT-11", self.source_name,
                        f"Oscilacao do pulso a {tremor_hz:.1f}Hz sustentada por mais de "
                        f"{self.seizure_config.hold_seconds:.0f}s (possivel convulsao/tremor).",
                        person_label=person_label,
                    ))
                    self._seizure_notified = True
            else:
                self._tremor_since = None
                self._seizure_notified = False

        return events
