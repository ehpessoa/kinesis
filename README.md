# 👁️ Projeto Kinesis — AI Vision & Behavior Analytics (POC)

O **Projeto Kinesis** é uma prova de conceito (POC) experimental desenvolvida em Python para **análise comportamental e cinemática multimodal em tempo real**, com suporte a múltiplas fontes de vídeo simultâneas: webcam local, câmera IP na rede Wi-Fi local e câmera IP remota acessada via VPN (Tailscale) sobre 5G/4G.

A solução ingere o feed de vídeo de cada fonte e executa inferências paralelas de pose, rosto e mãos (via [MediaPipe Tasks](https://ai.google.dev/edge/mediapipe/solutions/guide)) para correlacionar **reconhecimento facial**, **microexpressões emocionais**, **orientação da cabeça**, **postura corporal**, **gestos de mão** e **eventos dinâmicos críticos** (como quedas bruscas e sonolência), com renderização de telemetria em tempo real (HUD) — uma janela por câmera.

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
* **Multi-Fonte Concorrente:** Webcam local, câmeras IP em Wi-Fi local e câmeras IP remotas via VPN — todas processadas em paralelo, cada uma em sua própria thread de captura, com reconexão automática em caso de queda de sinal.
* **Biometria & Identificação:** Suporte a associação com identidades cadastradas (mock de ID no pipeline atual).
* **HUD (Head-Up Display) em Tempo Real:** Painel translúcido por câmera, integrado ao frame de vídeo, com todas as métricas acima, esqueleto de pose/rosto/mãos sobreposto e contador de FPS.

---

## 🏗️ Arquitetura do Pipeline

O pipeline roda integralmente em CPU local (Edge Computing), sem envio de vídeo para serviços externos.

```
config.py       -> carrega as fontes de câmera (cameras.json ou webcam padrão)
capture.py      -> ThreadedCamera: captura em thread própria, com reconexão automática
tracking.py     -> download de modelos, desenho de landmarks, BehaviorTracker
app_vision.py   -> orquestra N CameraPipeline (1 por fonte) e renderiza o HUD
```

Para cada fonte de câmera configurada, o `app_vision.py` cria um `CameraPipeline` **independente**, com:

1. **Captura** própria (`ThreadedCamera`), com reconexão automática e sem lag de rede.
2. **Detectores MediaPipe próprios** (`PoseLandmarker`, `FaceLandmarker`, `GestureRecognizer`), em modo `VIDEO`, alimentados com um **timestamp monotônico local** (contador de frames da própria fonte).
3. **`BehaviorTracker` próprio**, mantendo estado entre frames (histórico de quadris, velocidade dos punhos, tempo de olhos fechados) isolado por câmera.
4. **Renderização** em uma janela dedicada (`Kinesis - <nome da fonte>`).

> ⚠️ **Por que cada fonte tem seus próprios detectores?** O modo `VIDEO` do MediaPipe mantém estado temporal interno (suavização/tracking) assumindo um fluxo contínuo de uma única cena. Compartilhar um único detector entre câmeras diferentes corromperia esse estado e poderia gerar timestamps não-monotônicos durante reconexões — por isso a arquitetura usa uma instância isolada por fonte, e não uma instância global compartilhada.

Os modelos (`.task`) são baixados automaticamente na primeira execução para a pasta `models/` (não versionada) a partir do repositório oficial do Google.

---

## 📷 Cenários de Câmera Suportados

| Cenário | Fonte | Configuração |
|---|---|---|
| 1 | Webcam local do computador | `{"index": 0}` |
| 2 | Câmera IP (Intelbras Mibo) na mesma rede Wi-Fi local | `{"ip": "192.168.1.108", "password": "..."}` |
| 3 | Câmera IP remota, em rede 5G/4G, acessada via VPN Tailscale (subnet router) | `{"ip": "192.168.8.150", "password": "..."}` |

Do ponto de vista da aplicação, os **Cenários 2 e 3 são tecnicamente idênticos**: ambos são uma fonte RTSP genérica com reconexão automática. A VPN Tailscale apenas expõe o IP local da câmera remota como se estivesse na rede do computador — a diferença entre os dois cenários está inteiramente na camada de rede/hardware, não no código.

### Configurando as câmeras (`cameras.json`)

Copie o template e edite com os dados reais das suas câmeras:

```bash
cp cameras.example.json cameras.json
```

```json
[
  { "name": "Webcam Local (Cenario 1)", "index": 0 },
  { "name": "Intelbras Wi-Fi Local (Cenario 2)", "ip": "192.168.1.108", "user": "admin", "password": "CHAVE_ACESSO_1" },
  { "name": "Intelbras 5G Remota via Tailscale (Cenario 3)", "ip": "192.168.8.150", "user": "admin", "password": "CHAVE_ACESSO_2" }
]
```

* `index`: índice numérico de uma webcam local.
* `ip` + `user`/`password` (+ opcionalmente `port`, `channel`, `subtype`): monta automaticamente a URL RTSP padrão Intelbras/Dahua (`rtsp://user:password@ip:port/cam/realmonitor?channel=...&subtype=...`).
* `rtsp_url`: alternativa para informar uma URL RTSP completa e customizada.

O arquivo `cameras.json` **não é versionado** (está no `.gitignore`) justamente para não expor credenciais/IPs de câmera no repositório. Também é possível apontar para outro caminho de configuração via variável de ambiente `KINESIS_CAMERAS_CONFIG`. Se `cameras.json` não existir, o pipeline usa a webcam local (índice 0) por padrão.

---

## 🌐 Setup de Hardware (Câmera 5G Remota via Tailscale)

Para o Cenário 3 (câmera em rede 5G remota), a arquitetura recomendada usa um roteador Wi-Fi com chip 5G/4G atuando como *Subnet Router* do Tailscale, eliminando a necessidade de um Raspberry Pi na ponta da câmera:

1. **Câmera + roteador remoto:** insira o chip 5G no roteador (ex: GL.iNet GL-X3000), conecte a câmera IP (ex: Intelbras Mibo iM4-C) ao Wi-Fi gerado por ele via app Mibo Smart, e anote o IP local e a chave de acesso da câmera (etiqueta sob a base).
2. **VPN no roteador:** no painel do roteador (`192.168.8.1`), vá em VPN → Tailscale, faça login na sua conta, habilite *Allow Subnet Routes* para a sub-rede da câmera (ex: `192.168.8.0/24`) e aprove a rota em [login.tailscale.com](https://login.tailscale.com) → Machines → Edit route settings.
3. **Computador principal:** instale o cliente Tailscale, faça login com a mesma conta e ative a VPN — a partir daí o computador acessa a câmera remota pelo IP de sub-rede, através do túnel criptografado sobre 5G.

---

## ▶️ Como Executar

```bash
pip install opencv-python mediapipe numpy
cp cameras.example.json cameras.json   # edite com IPs/credenciais reais (opcional)
python app_vision.py
```

Pressione `q` ou `Esc` em qualquer janela de vídeo para encerrar todas as fontes. É necessário acesso à(s) câmera(s) e conexão à internet na primeira execução (download dos modelos).
