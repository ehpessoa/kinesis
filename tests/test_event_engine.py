from datetime import datetime

from config.schemas import NightRoutineConfig, SeizureDetectionConfig
from src.behavior.event_engine import EVENT_CATALOG, EventEngine, _is_within_night_window, _point_in_polygon


BASE_POSE = {
    "posture": "Em pe", "dynamic_state": "Estatico", "fall_alert": False,
    "arm_raised": False, "hand_near_face": False,
}
BASE_BLEND = {"emotion": "Neutro", "drowsy_alert": False, "distress_score": 0.0}


def make_engine(**overrides):
    defaults = dict(
        source_name="Teste",
        immobility_after_fall_seconds=1.0,
        sos_hold_seconds=0.3,
        hand_face_hold_seconds=0.3,
        distress_hold_seconds=0.3,
        inactivity_threshold_seconds=1.0,
    )
    defaults.update(overrides)
    return EventEngine(**defaults)


def test_event_catalog_has_ten_implemented_events():
    assert set(EVENT_CATALOG.keys()) == {
        "EVT-01", "EVT-02", "EVT-03", "EVT-04", "EVT-06", "EVT-07", "EVT-08", "EVT-09", "EVT-10", "EVT-11",
    }


def test_fall_then_immobility_fires_evt01_and_evt02():
    engine = make_engine()
    now = 1000.0

    events = engine.update({**BASE_POSE, "posture": "Deitado", "fall_alert": True}, BASE_BLEND, now=now)
    assert [e.event_id for e in events] == ["EVT-01"]

    fired = []
    for _ in range(15):
        now += 0.1
        fired += engine.update({**BASE_POSE, "posture": "Deitado", "fall_alert": False}, BASE_BLEND, now=now)

    assert "EVT-02" in [e.event_id for e in fired]


def test_fall_immobility_broken_by_movement_does_not_fire_evt02():
    engine = make_engine()
    now = 1000.0
    engine.update({**BASE_POSE, "posture": "Deitado", "fall_alert": True}, BASE_BLEND, now=now)

    fired = []
    for i in range(15):
        now += 0.1
        moving = {**BASE_POSE, "posture": "Deitado", "dynamic_state": "Ativo / Em Movimento"} if i == 5 else \
            {**BASE_POSE, "posture": "Deitado"}
        fired += engine.update(moving, BASE_BLEND, now=now)

    assert "EVT-02" not in [e.event_id for e in fired]


def test_drowsy_edge_fires_once_per_episode():
    engine = make_engine()
    now = 1000.0
    events = []
    events += engine.update(BASE_POSE, {**BASE_BLEND, "drowsy_alert": True}, now=now)
    now += 0.1
    events += engine.update(BASE_POSE, {**BASE_BLEND, "drowsy_alert": True}, now=now)
    now += 0.1
    events += engine.update(BASE_POSE, {**BASE_BLEND, "drowsy_alert": False}, now=now)
    now += 0.1
    events += engine.update(BASE_POSE, {**BASE_BLEND, "drowsy_alert": True}, now=now)

    ids = [e.event_id for e in events]
    assert ids.count("EVT-04") == 2  # uma borda de subida por episodio


def test_sos_gesture_sustained_fires_once():
    # inactivity_threshold_seconds alto para isolar o debounce do EVT-06:
    # braco levantado sozinho nao conta como "atividade" para o EVT-03 (ver
    # is_active em EventEngine.update), e este teste roda por mais de 1s
    # simulado — sem isso, EVT-03 dispararia junto e nao seria o que este
    # teste quer verificar.
    engine = make_engine(inactivity_threshold_seconds=60.0)
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "arm_raised": True}, BASE_BLEND, now=now)
    assert [e.event_id for e in fired] == ["EVT-06"]

    # solta o braco e levanta de novo -> dispara uma segunda vez
    now += 0.1
    engine.update(BASE_POSE, BASE_BLEND, now=now)
    fired2 = []
    for _ in range(5):
        now += 0.1
        fired2 += engine.update({**BASE_POSE, "arm_raised": True}, BASE_BLEND, now=now)
    assert [e.event_id for e in fired2] == ["EVT-06"]


def test_hand_near_face_sustained_fires_evt07():
    engine = make_engine()
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "hand_near_face": True}, BASE_BLEND, now=now)
    assert [e.event_id for e in fired] == ["EVT-07"]


def test_posture_change_fires_evt08_informative():
    engine = make_engine()
    now = 1000.0
    engine.update({**BASE_POSE, "posture": "Em pe"}, BASE_BLEND, now=now)
    now += 0.1
    events = engine.update({**BASE_POSE, "posture": "Sentado"}, BASE_BLEND, now=now)
    assert [e.event_id for e in events] == ["EVT-08"]
    assert "Em pe -> Sentado" in events[0].message


def test_distress_sustained_fires_evt09():
    engine = make_engine(distress_threshold=0.45)
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update(BASE_POSE, {**BASE_BLEND, "distress_score": 0.9}, now=now)
    assert [e.event_id for e in fired] == ["EVT-09"]


def test_inactivity_fires_evt03_after_threshold():
    engine = make_engine(inactivity_threshold_seconds=1.0)
    now = 1000.0
    fired = []
    for _ in range(15):
        now += 0.1
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now)
    assert "EVT-03" in [e.event_id for e in fired]


def test_person_label_propagates_to_event_notification():
    engine = make_engine()
    events = engine.update(
        {**BASE_POSE, "posture": "Deitado", "fall_alert": True}, BASE_BLEND,
        now=1000.0, person_label="Pessoa 42",
    )
    assert events[0].person_label == "Pessoa 42"


def test_person_label_defaults_to_none():
    engine = make_engine()
    events = engine.update(
        {**BASE_POSE, "posture": "Deitado", "fall_alert": True}, BASE_BLEND, now=1000.0,
    )
    assert events[0].person_label is None


# --- EVT-10: Ausencia da Cama no Horario Noturno ---

BED_ZONE = [(0.0, 0.0), (0.3, 0.0), (0.3, 0.3), (0.0, 0.3)]  # canto superior esquerdo do frame


def _epoch_at(hour, minute=0, day=12):
    # datetime(...).timestamp() interpreta o horario como local a maquina
    # que roda o teste, e EventEngine.update usa datetime.fromtimestamp()
    # (tambem local) - o round-trip devolve a mesma hora/minuto
    # independente do fuso horario real da maquina que executa a suite.
    return datetime(2026, 9, day, hour, minute, 0).timestamp()


def make_night_engine(night_routine_overrides=None, **overrides):
    night_config = NightRoutineConfig(
        enabled=True, bed_zone=BED_ZONE, night_start="22:00", night_end="06:00",
        absence_threshold_minutes=0.1,  # 6s - rapido o suficiente para o teste
        **(night_routine_overrides or {}),
    )
    return make_engine(night_routine_config=night_config, **overrides)


def test_evt10_fires_when_absent_from_bed_during_night_window():
    engine = make_night_engine()
    now = _epoch_at(23, 0)
    fired = []
    for _ in range(10):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=(0.9, 0.9))
    assert "EVT-10" in [e.event_id for e in fired]


def test_evt10_does_not_fire_when_person_in_bed_zone():
    engine = make_night_engine()
    now = _epoch_at(23, 0)
    fired = []
    for _ in range(10):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=(0.1, 0.1))
    assert "EVT-10" not in [e.event_id for e in fired]


def test_evt10_does_not_fire_outside_night_window():
    engine = make_night_engine()
    now = _epoch_at(14, 0)
    fired = []
    for _ in range(10):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=(0.9, 0.9))
    assert "EVT-10" not in [e.event_id for e in fired]


def test_evt10_counts_no_person_detected_as_absence():
    # Decisao deliberada (ver EventEngine.update): "sem pessoa detectada"
    # conta como ausencia, nao como "sem dado, ignorar" - e o cenario mais
    # obvio de deambulacao (saiu do campo de visao).
    engine = make_night_engine()
    now = _epoch_at(23, 0)
    fired = []
    for _ in range(10):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=None)
    assert "EVT-10" in [e.event_id for e in fired]


def test_evt10_disabled_by_default():
    engine = make_engine()  # NightRoutineConfig() default: enabled=False
    now = _epoch_at(23, 0)
    fired = []
    for _ in range(10):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=(0.9, 0.9))
    assert "EVT-10" not in [e.event_id for e in fired]


def test_evt10_fires_only_once_per_absence_episode():
    engine = make_night_engine()
    now = _epoch_at(23, 0)
    fired = []
    for _ in range(20):
        now += 1.0
        fired += engine.update(BASE_POSE, BASE_BLEND, now=now, person_point=(0.9, 0.9))
    assert [e.event_id for e in fired].count("EVT-10") == 1


def test_is_within_night_window_crossing_midnight():
    assert _is_within_night_window(datetime(2026, 1, 1, 23, 30), "22:00", "06:00") is True
    assert _is_within_night_window(datetime(2026, 1, 1, 5, 30), "22:00", "06:00") is True
    assert _is_within_night_window(datetime(2026, 1, 1, 12, 0), "22:00", "06:00") is False
    assert _is_within_night_window(datetime(2026, 1, 1, 22, 0), "22:00", "06:00") is True
    assert _is_within_night_window(datetime(2026, 1, 1, 6, 0), "22:00", "06:00") is False


def test_point_in_polygon_basic_square():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert _point_in_polygon(0.5, 0.5, square) is True
    assert _point_in_polygon(1.5, 0.5, square) is False


# --- EVT-11: Deteccao de Convulsao/Tremores ---

def make_seizure_engine(seizure_overrides=None, **overrides):
    seizure_config = SeizureDetectionConfig(
        enabled=True, freq_min_hz=2.0, freq_max_hz=6.0, min_amplitude=0.05, hold_seconds=0.3,
        **(seizure_overrides or {}),
    )
    return make_engine(seizure_config=seizure_config, **overrides)


def test_evt11_fires_when_tremor_in_band_sustained():
    engine = make_seizure_engine()
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": 4.0, "tremor_amplitude": 0.1}, BASE_BLEND, now=now)
    assert [e.event_id for e in fired] == ["EVT-11"]


def test_evt11_does_not_fire_when_frequency_outside_band():
    engine = make_seizure_engine()
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": 12.0, "tremor_amplitude": 0.1}, BASE_BLEND, now=now)
    assert "EVT-11" not in [e.event_id for e in fired]


def test_evt11_does_not_fire_when_amplitude_too_low():
    engine = make_seizure_engine()
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": 4.0, "tremor_amplitude": 0.01}, BASE_BLEND, now=now)
    assert "EVT-11" not in [e.event_id for e in fired]


def test_evt11_does_not_fire_when_tremor_hz_is_none():
    engine = make_seizure_engine()
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": None, "tremor_amplitude": 0.0}, BASE_BLEND, now=now)
    assert "EVT-11" not in [e.event_id for e in fired]


def test_evt11_disabled_by_default():
    engine = make_engine()  # SeizureDetectionConfig() default: enabled=False
    now = 1000.0
    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": 4.0, "tremor_amplitude": 0.5}, BASE_BLEND, now=now)
    assert "EVT-11" not in [e.event_id for e in fired]


def test_evt11_resets_when_frequency_leaves_band():
    engine = make_seizure_engine()
    now = 1000.0
    now += 0.1
    engine.update({**BASE_POSE, "tremor_hz": 4.0, "tremor_amplitude": 0.1}, BASE_BLEND, now=now)
    now += 0.1
    engine.update({**BASE_POSE, "tremor_hz": None, "tremor_amplitude": 0.0}, BASE_BLEND, now=now)

    fired = []
    for _ in range(5):
        now += 0.1
        fired += engine.update({**BASE_POSE, "tremor_hz": 4.0, "tremor_amplitude": 0.1}, BASE_BLEND, now=now)
    assert [e.event_id for e in fired] == ["EVT-11"]
