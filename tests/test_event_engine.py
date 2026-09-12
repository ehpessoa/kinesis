from src.behavior.event_engine import EVENT_CATALOG, EventEngine


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


def test_event_catalog_has_eight_implemented_events():
    assert set(EVENT_CATALOG.keys()) == {
        "EVT-01", "EVT-02", "EVT-03", "EVT-04", "EVT-06", "EVT-07", "EVT-08", "EVT-09",
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
