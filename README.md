# 👁️ Projeto Kinesis — AI Vision & Behavior Analytics (POC)

O **Projeto Kinesis** é uma prova de conceito (POC) experimental desenvolvida em Python para **análise comportamental e cinemática multimodal em tempo real**. 

A solução ingere feeds de vídeo (webcams, streams RTSP ou arquivos) e executa inferências paralelas para correlacionar **reconhecimento facial**, **microexpressões emocionais**, **postura corporal** e **eventos dinâmicos críticos** (como quedas bruscas, tropeços e inquietação), com renderização de telemetria em tempo real (HUD).

---

## 🚀 Funcionalidades Principais

* **Classificação de Emoções & Microexpressões:** Identificação contínua de estados afetivos (*Neutro, Alegria/Sorriso, Surpresa/Boca Aberta, Desconforto/Tristeza*).
* **Análise Postural Estática:** Detecção geométrica instantânea de corpo (*Em pé, Sentado, Deitado*).
* **Dinâmica de Movimento & Inquietação:** Estimativa de aceleração e dispersão de movimento de extremidades (*Estático, Ativo, Inquieto/Mexendo*).
* **Detector Cinemático de Quedas & Tropeços:** Identificação de colapsos verticais rápidos com transição abrupta de postura, emitindo alertas visuais imediatos.
* **Biometria & Identificação:** Suporte a extração de embeddings e associação com identidades cadastradas.
* **HUD (Head-Up Display) em Tempo Real:** Painel translúcido integrado ao frame de vídeo com métricas de telemetria e contador de FPS.

---

## 🏗️ Arquitetura do Pipeline

O pipeline foi projetado sob os princípios de **Edge Computing** e **Privacy by Design**, operando integralmente na memória volátil (RAM) sem persistência desnecessária de imagens brutas:
