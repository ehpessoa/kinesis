# 👁️ Projeto Kinesis — AI Vision & Behavior Analytics (POC)

O **Projeto Kinesis** é uma prova de conceito (POC) experimental desenvolvida em Python para **análise comportamental e cinemática multimodal em tempo real**.

A solução ingere o feed de vídeo da webcam e executa inferências paralelas de pose, rosto e mãos (via [MediaPipe Tasks](https://ai.google.dev/edge/mediapipe/solutions/guide)) para correlacionar **reconhecimento facial**, **microexpressões emocionais**, **orientação da cabeça**, **postura corporal**, **gestos de mão** e **eventos dinâmicos críticos** (como quedas bruscas e sonolência), com renderização de telemetria em tempo real (HUD).

---

## 🚀 Funcionalidades Principais

### Rosto (`FaceLandmarker` — 468 pontos + 52 blendshapes)
* **Classificação de Emoções:** Neutro, Alegria/Sorriso, Surpreso, Triste, Raiva/Tensão e Nojo/Desagrado, a partir dos coeficientes de expressão facial (*blendshapes*) do modelo.
* **Orientação da Cabeça:** Estimativa de direção do olhar/cabeça (*Frente, Cima, Baixo, Esquerda, Direita*) via geometria dos pontos faciais.
* **Detecção de Sonolência/Fadiga:** Monitoramento do fechamento prolongado dos olhos (piscar sustentado), com alerta visual dedicado.

### Corpo (`PoseLandmarker` — 33 pontos)
* **Análise Postural Estática:** Detecção geométrica instantânea de corpo (*Em pé, Sentado, Deitado*).
* **Dinâmica de Movimento & Inquietação:** Estimativa de velocidade e dispersão de movimento dos punhos (*Estático, Ativo/Em Movimento, Inquieto/Mexendo*).
* **Detector Cinemático de Quedas:** Identificação de colapsos verticais rápidos com transição abrupta de postura, emitindo alertas visuais imediatos.
* **Braço Levantado:** Detecção de elevação de qualquer um dos braços acima da linha dos ombros.
* **Mão no Rosto:** Detecção de proximidade do punho em relação ao rosto (indicativo de estresse, pensamento ou desconforto).

### Mãos (`GestureRecognizer` — até 2 mãos, com lateralidade)
* **Reconhecimento de Gestos:** Punho Fechado, Palma Aberta, Apontando para Cima, Joinha Positivo/Negativo, Sinal de Vitória e "Eu Te Amo", identificados por mão (Esquerda/Direita).

### Geral
* **Biometria & Identificação:** Suporte a associação com identidades cadastradas (mock de ID no pipeline atual).
* **HUD (Head-Up Display) em Tempo Real:** Painel translúcido integrado ao frame de vídeo com todas as métricas acima, esqueleto de pose/rosto/mãos sobreposto e contador de FPS.

---

## 🏗️ Arquitetura do Pipeline

O pipeline roda integralmente em CPU local (Edge Computing), sem envio de vídeo para serviços externos:

1. **Captura:** frame da webcam via OpenCV, espelhado para exibição em modo selfie.
2. **Inferência paralela por frame**, usando a MediaPipe Tasks API em modo `VIDEO`:
   * `PoseLandmarker` (`pose_landmarker_lite.task`)
   * `FaceLandmarker` com `output_face_blendshapes=True` (`face_landmarker.task`)
   * `GestureRecognizer` (`gesture_recognizer.task`)
3. **Análise comportamental:** a classe `BehaviorTracker` consome os landmarks/blendshapes de cada frame e mantém estado entre frames (histórico de quadris e velocidade dos punhos, tempo com olhos fechados) para gerar as métricas e alertas.
4. **Renderização:** esqueletos e HUD sobrepostos ao frame com OpenCV, exibidos em uma janela local.

Os modelos (`.task`) são baixados automaticamente na primeira execução para a pasta `models/` (não versionada) a partir do repositório oficial do Google.

---

## ▶️ Como Executar

```bash
pip install opencv-python mediapipe numpy
python app_vision.py
```

Pressione `q` ou `Esc` na janela de vídeo para encerrar. É necessário acesso à webcam e conexão à internet na primeira execução (download dos modelos).
