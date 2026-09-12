"""Motor de Regras e Eventos (Matriz de Monitoramento).

Implementa 8 dos 12 eventos da matriz assistencial (EVT-01, 02, 03, 04, 06, 07,
08, 09), todos deriváveis das métricas que o BehaviorTracker já calcula por
frame. Os 4 eventos restantes exigem capacidades que a PoC/refatoração atual
ainda não tem e por isso NÃO estão implementados aqui (evitando um "meio
pronto" que pareça funcionar sem funcionar de verdade):

  - EVT-05 (Desorientação/Confusão): precisa de um classificador de padrão de
    olhar/cabeça errático sustentado por ~60s — ainda não modelado.
  - EVT-10 (Ausência da cama à noite): precisa de uma "zona da cama" e janela
    horária configuráveis (zona espacial no frame), que não existem ainda.
  - EVT-11 (Convulsão/Tremores): precisa de análise em frequência (ex: FFT ou
    contagem de cruzamentos de zero) da oscilação de pulsos/cotovelos, que é
    um algoritmo novo, não uma extensão do que já existe.
  - EVT-12 (Zona de risco): precisa de zonas poligonais configuráveis no
    frame (escada, fogão) e de um verificador de ponto-em-polígono.

Cada instância de EventEngine deve ser dedicada a UMA fonte de câmera — o
estado de debounce (timers de "desde quando") não pode ser compartilhado
entre pessoas/câmeras diferentes.
"""
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel

EVENT_CATALOG: Dict[str, Dict[str, str]] = {
    "EVT-01": {"category": "Queda", "name": "Queda Brusca Detectada", "default_severity": "CRITICAL"},
    "EVT-02": {"category": "Saude", "name": "Imobilidade Prolongada pos-queda", "default_severity": "CRITICAL"},
    "EVT-03": {"category": "Saude", "name": "Inatividade Geral Excessiva", "default_severity": "MEDIUM"},
    "EVT-04": {"category": "Atencao", "name": "Sonolencia / Olhos Fechados", "default_severity": "MEDIUM"},
    "EVT-06": {"category": "Gestos", "name": "Gesto de SOS / Pedido de Ajuda", "default_severity": "HIGH"},
    "EVT-07": {"category": "Gestos", "name": "Mao no Rosto / Mal-Estar", "default_severity": "MEDIUM"},
    "EVT-08": {"category": "Postura", "name": "Mudanca de Postura", "default_severity": "INFO"},
    "EVT-09": {"category": "Emocao", "name": "Expressao de Dor / Distress", "default_severity": "MEDIUM"},
}


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
    ):
        self.source_name = source_name
        self.immobility_after_fall_seconds = immobility_after_fall_seconds
        self.inactivity_threshold_seconds = inactivity_threshold_seconds
        self.sos_hold_seconds = sos_hold_seconds
        self.hand_face_hold_seconds = hand_face_hold_seconds
        self.distress_threshold = distress_threshold
        self.distress_hold_seconds = distress_hold_seconds

        self._prev_fall_alert = False
        self._fall_episode_started_at: Optional[float] = None
        self._fall_episode_broken = False

        self._last_activity_at = time.time()
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
        person_label: Optional[str] = None,
    ) -> List[EventNotification]:
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

        return events
