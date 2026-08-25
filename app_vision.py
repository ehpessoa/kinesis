import cv2
import mediapipe as mp
import numpy as np
import time
from collections import deque

class BehaviorTracker:
    """Gerencia a máquina de estados, cinemática corporal e expressões faciais."""
    def __init__(self, history_len: int = 15):
        self.history_len = history_len
        self.hip_y_history = deque(maxlen=history_len)
        self.wrist_speed_history = deque(maxlen=history_len)
        self.prev_wrists = None
        self.fall_state = False
        self.fall_timestamp = 0
        self.registered_id = "Usuario_Principal" # Mock de Biometria para o Piloto

    def analyze_face(self, face_landmarks, frame_w, frame_h) -> str:
        """
        Analisa marcos faciais para classificar expressões básicas
        (Alegria, Surpreso, Neutro, Boca Aberta/Tristeza).
        """
        # Índices de referência do FaceMesh
        # Topo/Base do lábio: 13, 14 | Cantos da boca: 61, 291
        # Sobrancelhas: 70, 300 | Olhos: 159, 386
        p13 = np.array([face_landmarks.landmark[13].x * frame_w, face_landmarks.landmark[13].y * frame_h])
        p14 = np.array([face_landmarks.landmark[14].x * frame_w, face_landmarks.landmark[14].y * frame_h])
        p61 = np.array([face_landmarks.landmark[61].x * frame_w, face_landmarks.landmark[61].y * frame_h])
        p291 = np.array([face_landmarks.landmark[291].x * frame_w, face_landmarks.landmark[291].y * frame_h])

        mouth_height = np.linalg.norm(p13 - p14)
        mouth_width = np.linalg.norm(p61 - p291)
        mouth_ratio = mouth_height / max(1.0, mouth_width)

        # Curvatura dos cantos da boca em relação ao centro labial
        mouth_center_y = (p13[1] + p14[1]) / 2.0
        corners_y = (p61[1] + p291[1]) / 2.0
        smile_metric = corners_y - mouth_center_y # Cantos mais altos que o centro indicam sorriso

        if mouth_ratio > 0.45:
            return "Surpreso / Boca Aberta"
        elif smile_metric < -1.5:
            return "Alegria / Sorriso"
        elif smile_metric > 3.0:
            return "Triste / Desconforto"
        else:
            return "Neutro"

    def analyze_pose(self, pose_landmarks, frame_w, frame_h) -> dict:
        """
        Calcula a cinemática do corpo: posturas, dinâmica de movimento e quedas bruscas.
        """
        # Extrai pontos-chave
        lms = pose_landmarks.landmark
        
        # Coordenadas chave
        nose = np.array([lms[0].x * frame_w, lms[0].y * frame_h])
        l_sh = np.array([lms[11].x * frame_w, lms[11].y * frame_h])
        r_sh = np.array([lms[12].x * frame_w, lms[12].y * frame_h])
        l_hip = np.array([lms[23].x * frame_w, lms[23].y * frame_h])
        r_hip = np.array([lms[24].x * frame_w, lms[24].y * frame_h])
        l_knee = np.array([lms[25].x * frame_w, lms[25].y * frame_h])
        r_knee = np.array([lms[26].x * frame_w, lms[26].y * frame_h])
        l_wrist = np.array([lms[15].x * frame_w, lms[15].y * frame_h])
        r_wrist = np.array([lms[16].x * frame_w, lms[16].y * frame_h])

        # Centros
        shoulder_center = (l_sh + r_sh) / 2.0
        hip_center = (l_hip + r_hip) / 2.0
        knee_center = (l_knee + r_knee) / 2.0

        # Caixa delimitadora aproximada
        torso_height = np.linalg.norm(shoulder_center - hip_center)
        body_width = np.linalg.norm(l_sh - r_sh)

        # 1. Ângulo do tronco em relação à vertical
        dx = abs(shoulder_center[0] - hip_center[0])
        dy = max(1.0, abs(shoulder_center[1] - hip_center[1]))
        trunk_angle = np.degrees(np.arctan(dx / dy))

        # 2. Identificação de Postura
        posture = "Em pe"
        if trunk_angle > 50.0 or dy < (body_width * 0.6):
            posture = "Deitado"
        else:
            hip_knee_dy = knee_center[1] - hip_center[1]
            if hip_knee_dy < (torso_height * 0.7):
                posture = "Sentado"

        # 3. Análise de Inquietação / Movimento das mãos
        current_wrists = (l_wrist + r_wrist) / 2.0
        if self.prev_wrists is not None:
            w_speed = np.linalg.norm(current_wrists - self.prev_wrists)
            self.wrist_speed_history.append(w_speed)
        self.prev_wrists = current_wrists

        avg_wrist_motion = np.mean(self.wrist_speed_history) if self.wrist_speed_history else 0
        
        dynamic_state = "Estatico"
        if avg_wrist_motion > 15.0:
            dynamic_state = "Inquieto / Mexendo"
        elif avg_wrist_motion > 4.0:
            dynamic_state = "Ativo / Em Movimento"

        # 4. Detecção de Queda Brusca e Tropeço
        self.hip_y_history.append(hip_center[1])
        fall_alert = False

        if len(self.hip_y_history) == self.history_len:
            dy_descent = self.hip_y_history[-1] - self.hip_y_history[0]
            # Queda rápida: deslocamento vertical brusco para baixo seguido de postura deitada
            if dy_descent > (torso_height * 0.8) and posture == "Deitado":
                self.fall_state = True
                self.fall_timestamp = time.time()

        # Mantém o alerta visível por 3 segundos
        if self.fall_state:
            if time.time() - self.fall_timestamp < 3.0:
                fall_alert = True
            else:
                self.fall_state = False

        return {
            "posture": posture,
            "dynamic_state": dynamic_state,
            "fall_alert": fall_alert,
            "torso_h": torso_height
        }


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Erro: Não foi possível acessar a câmera do computador.")
        return

    # Inicialização dos detectores MediaPipe
    mp_pose = mp.solutions.pose
    mp_face_mesh = mp.solutions.face_mesh
    mp_drawing = mp.solutions.drawing_utils
    mp_drawing_styles = mp.solutions.drawing_styles

    pose_detector = mp_pose.Pose(
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
        model_complexity=1
    )
    face_detector = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )

    tracker = BehaviorTracker()
    prev_frame_time = time.time()

    print("Pipeline iniciado com sucesso. Pressione 'q' na janela para encerrar.")

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Espelhamento horizontal para experiência natural de webcam
        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        # Conversão de cores para processamento no MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb_frame.flags.writeable = False

        pose_results = pose_detector.process(rgb_frame)
        face_results = face_detector.process(rgb_frame)

        # Variáveis de exibição
        emotion = "N/A"
        posture = "N/A"
        motion = "N/A"
        fall_alert = False

        # 1. Extração de Expressão Facial
        if face_results.multi_face_landmarks:
            for face_landmarks in face_results.multi_face_landmarks:
                emotion = tracker.analyze_face(face_landmarks, w, h)
                # Desenha malha facial sutil
                mp_drawing.draw_landmarks(
                    image=frame,
                    landmark_list=face_landmarks,
                    connections=mp_face_mesh.FACEMESH_CONTOURS,
                    landmark_drawing_spec=None,
                    connection_drawing_spec=mp_drawing_styles.get_default_face_mesh_contours_style()
                )

        # 2. Extração de Postura e Movimento Corporal
        if pose_results.pose_landmarks:
            mp_drawing.draw_landmarks(
                frame,
                pose_results.pose_landmarks,
                mp_pose.POSE_CONNECTIONS,
                landmark_drawing_spec=mp_drawing_styles.get_default_pose_landmarks_style()
            )
            pose_data = tracker.analyze_pose(pose_results.pose_landmarks, w, h)
            posture = pose_data["posture"]
            motion = pose_data["dynamic_state"]
            fall_alert = pose_data["fall_alert"]

        # 3. Cálculo de FPS
        curr_frame_time = time.time()
        fps = 1.0 / max(1e-5, (curr_frame_time - prev_frame_time))
        prev_frame_time = curr_frame_time

        # --- Camada de Renderização do HUD (Interface em Tempo Real) ---
        # Painel Lateral de Informações
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, 10), (340, 210), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

        # Textos informativos
        cv2.putText(frame, f"ID: {tracker.registered_id}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
        cv2.putText(frame, f"Expressao: {emotion}", (20, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
        cv2.putText(frame, f"Postura: {posture}", (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (100, 255, 100), 2)
        cv2.putText(frame, f"Movimento: {motion}", (20, 145), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 200, 100), 2)
        cv2.putText(frame, f"FPS: {fps:.1f}", (20, 185), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)

        # Alerta de Queda / Evento Crítico
        if fall_alert:
            cv2.rectangle(frame, (0, 0), (w, h), (0, 0, 255), 6)
            cv2.putText(frame, "ALERTA: QUEDA BRUSCA DETECTADA!", (w // 2 - 240, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.85, (0, 0, 255), 3)

        cv2.imshow("Monitoramento de Comportamento e Biometria em Tempo Real", frame)

        # Tecla 'q' ou 'ESC' para sair
        if cv2.waitKey(1) & 0xFF in [ord('q'), 27]:
            break

    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
