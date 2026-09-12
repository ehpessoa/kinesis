"""Máquina de estados, cinemática corporal e expressões faciais de UMA única
fonte de vídeo. Instancie um BehaviorTracker por câmera/pessoa monitorada —
compartilhar a mesma instância entre fontes misturaria o estado de pessoas
ou cenas diferentes."""
import time
from collections import deque
from typing import Optional, Tuple

import numpy as np

from src.vision.detectors import GESTURE_LABELS

# Numero minimo de amostras na janela de tremor para uma estimativa de
# frequencia minimamente confiavel via FFT - abaixo disso o resultado seria
# ruido (poucos pontos, poucos bins de frequencia).
_MIN_TREMOR_SAMPLES = 8
_MIN_TREMOR_WINDOW_DURATION_SECONDS = 1.0


class BehaviorTracker:
    def __init__(self, history_len: int = 15, tremor_window_seconds: float = 4.0):
        self.history_len = history_len
        self.hip_y_history = deque(maxlen=history_len)
        self.wrist_speed_history = deque(maxlen=history_len)
        self.prev_wrists = None
        self.fall_state = False
        self.fall_timestamp = 0
        self.eyes_closed_since = None
        # Sobrescrito a cada frame pela CameraPipeline com o ID de
        # rastreamento continuo (ByteTrack) da pessoa principal em cena —
        # ver src/vision/person_tracker.py. Nao ha reidentificacao facial.
        # Fica "N/A" se person_tracking estiver desabilitado na config.
        self.registered_id = "N/A"

        # EVT-11 (convulsao/tremor): historico com timestamp (nao so
        # contagem de amostras, ao contrario de wrist_speed_history acima)
        # porque a analise em frequencia precisa da taxa de amostragem real,
        # que varia com pipeline_rates.pose_fps e com jitter de rede em RTSP.
        # maxlen generoso apenas como teto de seguranca; o filtro por tempo
        # em _analyze_tremor() e o que de fato define a janela analisada.
        self.tremor_window_seconds = tremor_window_seconds
        self.wrist_track_history = deque(maxlen=200)

    def analyze_blendshapes(self, blendshapes) -> dict:
        scores = {c.category_name: c.score for c in blendshapes}

        smile = (scores.get("mouthSmileLeft", 0) + scores.get("mouthSmileRight", 0)) / 2
        frown = (scores.get("mouthFrownLeft", 0) + scores.get("mouthFrownRight", 0)) / 2
        brow_down = (scores.get("browDownLeft", 0) + scores.get("browDownRight", 0)) / 2
        brow_up = scores.get("browInnerUp", 0)
        jaw_open = scores.get("jawOpen", 0)
        eye_wide = (scores.get("eyeWideLeft", 0) + scores.get("eyeWideRight", 0)) / 2
        nose_sneer = (scores.get("noseSneerLeft", 0) + scores.get("noseSneerRight", 0)) / 2
        mouth_press = (scores.get("mouthPressLeft", 0) + scores.get("mouthPressRight", 0)) / 2
        eye_blink = (scores.get("eyeBlinkLeft", 0) + scores.get("eyeBlinkRight", 0)) / 2

        if nose_sneer > 0.4:
            emotion = "Nojo / Desagrado"
        elif jaw_open > 0.5 and (brow_up > 0.3 or eye_wide > 0.3):
            emotion = "Surpreso"
        elif smile > 0.4:
            emotion = "Alegria / Sorriso"
        elif frown > 0.3 and brow_up > 0.25:
            emotion = "Triste"
        elif brow_down > 0.4 and mouth_press > 0.2:
            emotion = "Raiva / Tensao"
        else:
            emotion = "Neutro"

        # EVT-09: indicador de dor/distress, independente do rotulo mutuamente
        # exclusivo de "emotion" acima (dor pode coexistir com outras leituras).
        distress_score = (brow_down + frown + nose_sneer) / 3.0

        drowsy_alert = False
        now = time.time()
        if eye_blink > 0.55:
            if self.eyes_closed_since is None:
                self.eyes_closed_since = now
            elif now - self.eyes_closed_since > 1.2:
                drowsy_alert = True
        else:
            self.eyes_closed_since = None

        return {"emotion": emotion, "drowsy_alert": drowsy_alert, "distress_score": distress_score}

    def estimate_head_pose(self, face_landmarks, frame_w, frame_h) -> str:
        nose = face_landmarks[1]
        forehead = face_landmarks[10]
        chin = face_landmarks[152]
        left_edge = face_landmarks[234]
        right_edge = face_landmarks[454]

        face_span_x = max(1.0, (right_edge.x - left_edge.x) * frame_w)
        horizontal_ratio = ((nose.x * frame_w) - (left_edge.x * frame_w)) / face_span_x

        face_span_y = max(1.0, (chin.y - forehead.y) * frame_h)
        vertical_ratio = ((nose.y * frame_h) - (forehead.y * frame_h)) / face_span_y

        if horizontal_ratio < 0.35:
            return "Direita"
        elif horizontal_ratio > 0.65:
            return "Esquerda"
        elif vertical_ratio > 0.62:
            return "Baixo"
        elif vertical_ratio < 0.45:
            return "Cima"
        else:
            return "Frente"

    def analyze_pose(self, pose_landmarks, frame_w, frame_h) -> dict:
        lms = pose_landmarks

        nose = np.array([lms[0].x * frame_w, lms[0].y * frame_h])
        l_sh = np.array([lms[11].x * frame_w, lms[11].y * frame_h])
        r_sh = np.array([lms[12].x * frame_w, lms[12].y * frame_h])
        l_hip = np.array([lms[23].x * frame_w, lms[23].y * frame_h])
        r_hip = np.array([lms[24].x * frame_w, lms[24].y * frame_h])
        l_knee = np.array([lms[25].x * frame_w, lms[25].y * frame_h])
        r_knee = np.array([lms[26].x * frame_w, lms[26].y * frame_h])
        l_wrist = np.array([lms[15].x * frame_w, lms[15].y * frame_h])
        r_wrist = np.array([lms[16].x * frame_w, lms[16].y * frame_h])

        shoulder_center = (l_sh + r_sh) / 2.0
        hip_center = (l_hip + r_hip) / 2.0
        knee_center = (l_knee + r_knee) / 2.0

        torso_height = np.linalg.norm(shoulder_center - hip_center)
        body_width = np.linalg.norm(l_sh - r_sh)

        dx = abs(shoulder_center[0] - hip_center[0])
        dy = max(1.0, abs(shoulder_center[1] - hip_center[1]))
        trunk_angle = np.degrees(np.arctan(dx / dy))

        posture = "Em pe"
        if trunk_angle > 50.0 or dy < (body_width * 0.6):
            posture = "Deitado"
        else:
            hip_knee_dy = knee_center[1] - hip_center[1]
            if hip_knee_dy < (torso_height * 0.7):
                posture = "Sentado"

        current_wrists = (l_wrist + r_wrist) / 2.0
        if self.prev_wrists is not None:
            w_speed = np.linalg.norm(current_wrists - self.prev_wrists)
            self.wrist_speed_history.append(w_speed)
        self.prev_wrists = current_wrists

        now_ts = time.time()
        self.wrist_track_history.append((now_ts, float(current_wrists[0]), float(current_wrists[1])))
        tremor_hz, tremor_amplitude = self._analyze_tremor(now_ts, torso_height)

        avg_wrist_motion = np.mean(self.wrist_speed_history) if self.wrist_speed_history else 0

        dynamic_state = "Estatico"
        if avg_wrist_motion > 15.0:
            dynamic_state = "Inquieto / Mexendo"
        elif avg_wrist_motion > 4.0:
            dynamic_state = "Ativo / Em Movimento"

        arm_raised = bool(
            (l_wrist[1] < shoulder_center[1] - torso_height * 0.2)
            or (r_wrist[1] < shoulder_center[1] - torso_height * 0.2)
        )
        hand_near_face = bool(
            (np.linalg.norm(l_wrist - nose) < body_width * 0.4)
            or (np.linalg.norm(r_wrist - nose) < body_width * 0.4)
        )

        self.hip_y_history.append(hip_center[1])
        fall_alert = False

        if len(self.hip_y_history) == self.history_len:
            dy_descent = self.hip_y_history[-1] - self.hip_y_history[0]
            if dy_descent > (torso_height * 0.8) and posture == "Deitado":
                self.fall_state = True
                self.fall_timestamp = time.time()

        if self.fall_state:
            if time.time() - self.fall_timestamp < 3.0:
                fall_alert = True
            else:
                self.fall_state = False

        return {
            "posture": posture,
            "dynamic_state": dynamic_state,
            "fall_alert": fall_alert,
            "arm_raised": arm_raised,
            "hand_near_face": hand_near_face,
            "torso_h": torso_height,
            "tremor_hz": tremor_hz,
            "tremor_amplitude": tremor_amplitude,
        }

    def _analyze_tremor(self, now: float, torso_height: float) -> Tuple[Optional[float], float]:
        """Estima a frequência dominante de oscilação do pulso na janela
        recente (`tremor_window_seconds`) via FFT — mitigação do EVT-11
        (convulsão/tremor), que o plano original descreve como exigindo
        "análise em frequência (FFT/zero-crossing) da oscilação de
        pulsos/cotovelos". Usa o ponto médio entre os dois pulsos (mesmo
        agregado já usado por `wrist_speed_history` acima) — uma
        simplificação real: oscilação em anti-fase entre os braços poderia,
        em tese, se cancelar nessa média. Não implementado aqui por
        simplicidade; ver EventEngine/README para essa ressalva.

        Analisa só o eixo vertical (y) do pulso, não a magnitude 2D do
        deslocamento — tremor puramente horizontal não seria capturado.
        Escolha deliberada: a maioria dos relatos de movimento clônico tem
        componente vertical dominante (flexão/extensão), e um sinal 2D
        exigiria uma definição de "frequência" mais complexa (a distância
        euclidiana a um ponto de referência oscila ao dobro da frequência
        real do movimento, por retificação).

        Retorna `(None, 0.0)` quando não há amostras suficientes na janela
        para uma estimativa minimamente confiável (ex: logo após o início
        do rastreamento, ou pose_fps muito baixo)."""
        window = [(t, x, y) for t, x, y in self.wrist_track_history if now - t <= self.tremor_window_seconds]
        if len(window) < _MIN_TREMOR_SAMPLES or torso_height < 1e-3:
            return None, 0.0

        timestamps = np.array([t for t, _, _ in window])
        ys = np.array([y for _, _, y in window])

        duration = timestamps[-1] - timestamps[0]
        if duration < _MIN_TREMOR_WINDOW_DURATION_SECONDS:
            return None, 0.0

        # Taxa de amostragem efetiva medida (nao assumida) - absorve jitter
        # de captura RTSP e a taxa real configurada em pipeline_rates.pose_fps.
        sample_rate_hz = (len(timestamps) - 1) / duration

        signal = ys - np.mean(ys)
        # Amplitude normalizada pela altura do tronco: o mesmo deslocamento
        # em pixels significa "tremor maior" numa pessoa perto da camera do
        # que numa pessoa longe - a normalizacao torna o limiar independente
        # da distancia/enquadramento da instalacao.
        amplitude = float(np.std(signal) / torso_height)

        spectrum = np.abs(np.fft.rfft(signal))
        freqs = np.fft.rfftfreq(len(signal), d=1.0 / sample_rate_hz)
        if len(freqs) < 2:
            return None, amplitude

        # Ignora o indice 0 (componente DC/media, sempre o maior e sem
        # informacao de oscilacao) ao procurar o pico dominante.
        dominant_idx = int(np.argmax(spectrum[1:])) + 1
        dominant_hz = float(freqs[dominant_idx])

        return dominant_hz, amplitude

    def analyze_gestures(self, gesture_results) -> dict:
        hands = {}
        for i in range(len(gesture_results.hand_landmarks)):
            handedness = gesture_results.handedness[i][0].category_name if gesture_results.handedness[i] else None
            gesture_cat = gesture_results.gestures[i][0] if gesture_results.gestures[i] else None
            side = "Esquerda" if handedness == "Left" else "Direita"
            label = GESTURE_LABELS.get(gesture_cat.category_name) if gesture_cat else None
            if label:
                hands[side] = label
        return hands
