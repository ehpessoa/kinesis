let bridge = null;
let state = null;

const videoImg = new Image();
const canvas = document.getElementById("video-canvas");
const ctx = canvas.getContext("2d");
const placeholder = document.getElementById("video-placeholder");
const videoFrame = document.getElementById("video-frame");
const hud = document.getElementById("video-hud");
const overlaySvg = document.getElementById("video-overlay-svg");
const overlayMarks = document.getElementById("overlay-marks");

const SEVERITY_LABELS = { CRITICAL: "Crítico", HIGH: "Alto", MEDIUM: "Médio", LOW: "Baixo" };

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function severityClass(sev) {
  return `sev-${(sev || "info").toLowerCase()}`;
}

function severityLabel(sev) {
  return SEVERITY_LABELS[(sev || "").toUpperCase()] || sev || "Info";
}

function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    });
  });
}

function renderCameraSelect() {
  const select = document.getElementById("camera-select");
  select.innerHTML = "";
  state.cameras.forEach((cam) => {
    const opt = document.createElement("option");
    opt.value = cam.index;
    opt.textContent = cam.name;
    if (cam.index === state.selected_index) opt.selected = true;
    select.appendChild(opt);
  });
  select.onchange = () => {
    hud.classList.add("hidden");
    clearOverlay();
    bridge.select_camera(parseInt(select.value, 10));
  };
}

function statusCard(label, dotClass, text) {
  const div = document.createElement("div");
  div.className = "status-card";
  div.innerHTML = `<span class="status-dot ${dotClass}"></span>` +
    `<div><div class="status-label">${escapeHtml(label)}</div>` +
    `<div class="status-text">${escapeHtml(text)}</div></div>`;
  return div;
}

function renderStatusCards() {
  const container = document.getElementById("status-cards");
  container.innerHTML = "";
  state.status.cameras.forEach((cam) => {
    container.appendChild(statusCard(cam.name, cam.connected ? "on" : "off", cam.connected ? "Online" : "Sem sinal"));
  });
  container.appendChild(statusCard(
    "WhatsApp API", state.status.whatsapp_configured ? "on" : "off",
    state.status.whatsapp_configured ? "Configurado" : "Sem endpoint",
  ));
  container.appendChild(statusCard(
    "Voz", state.status.voice_available ? "on" : "na",
    state.status.voice_available ? "Ativo" : "Desabilitado",
  ));
}

function renderEventsTable() {
  const tbody = document.getElementById("events-tbody");
  tbody.innerHTML = "";
  Object.values(state.events).sort((a, b) => a.id.localeCompare(b.id)).forEach((ev) => {
    const tr = document.createElement("tr");
    tr.className = ev.implemented ? "" : "unimplemented";
    const contactsOptions = state.contacts.map((c) =>
      `<option value="${c.id}" ${ev.notify_contact_ids.includes(c.id) ? "selected" : ""}>${escapeHtml(c.name)}</option>`
    ).join("");
    tr.innerHTML = `
      <td>
        <div class="event-name">${ev.id}</div>
        <div class="event-id">${escapeHtml(ev.name)}</div>
      </td>
      <td class="dim">${escapeHtml(ev.category)}</td>
      <td><span class="sev-pill ${severityClass(ev.severity)}">${severityLabel(ev.severity)}</span></td>
      <td><input type="checkbox" class="toggle" ${ev.enabled ? "checked" : ""} ${ev.implemented ? "" : "disabled"} data-event="${ev.id}" /></td>
      <td><select multiple class="contacts-select" data-event-contacts="${ev.id}" ${ev.implemented ? "" : "disabled"}>${contactsOptions}</select></td>
      <td><button class="btn-secondary" data-test-event="${ev.id}">Testar</button></td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll("input[data-event]").forEach((input) => {
    input.addEventListener("change", () => bridge.set_event_enabled(input.dataset.event, input.checked));
  });
  tbody.querySelectorAll("select[data-event-contacts]").forEach((select) => {
    select.addEventListener("change", () => {
      const ids = Array.from(select.selectedOptions).map((o) => o.value);
      bridge.set_event_contacts(select.dataset.eventContacts, JSON.stringify(ids));
    });
  });
  tbody.querySelectorAll("button[data-test-event]").forEach((btn) => {
    btn.addEventListener("click", () => bridge.test_alert(btn.dataset.testEvent));
  });
}

function renderContactsTable() {
  const tbody = document.getElementById("contacts-tbody");
  tbody.innerHTML = "";
  state.contacts.forEach((c) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(c.name)}</td>
      <td class="dim">${escapeHtml(c.relationship || "-")}</td>
      <td class="dim">${escapeHtml(c.whatsapp_number || "-")}</td>
      <td style="text-align:right"><button class="btn-secondary" data-delete-contact="${c.id}">Remover</button></td>
    `;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("button[data-delete-contact]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const contactId = btn.dataset.deleteContact;
      bridge.delete_contact(contactId, (result) => {
        const res = JSON.parse(result);
        if (res.ok) {
          state.contacts = state.contacts.filter((c) => c.id !== contactId);
          renderContactsTable();
          renderEventsTable();
        }
      });
    });
  });
}

function renderWhatsAppConfig() {
  document.getElementById("cfg-endpoint").value = state.whatsapp.endpoint || "";
  document.getElementById("cfg-instance").value = state.whatsapp.instance || "";
}

function initContactModal() {
  const modal = document.getElementById("contact-modal");
  document.getElementById("btn-add-contact").addEventListener("click", () => {
    document.getElementById("modal-name").value = "";
    document.getElementById("modal-relationship").value = "";
    document.getElementById("modal-number").value = "";
    modal.classList.remove("hidden");
  });
  document.getElementById("modal-cancel").addEventListener("click", () => modal.classList.add("hidden"));
  document.getElementById("modal-save").addEventListener("click", () => {
    const payload = {
      name: document.getElementById("modal-name").value.trim(),
      relationship: document.getElementById("modal-relationship").value.trim(),
      whatsapp_number: document.getElementById("modal-number").value.trim(),
    };
    if (!payload.name) return;
    bridge.add_contact(JSON.stringify(payload), (result) => {
      const res = JSON.parse(result);
      if (res.ok) {
        state.contacts.push(res.contact);
        renderContactsTable();
        renderEventsTable();
        modal.classList.add("hidden");
      }
    });
  });
}

function initWhatsAppForm() {
  document.getElementById("btn-save-whatsapp").addEventListener("click", () => {
    const payload = {
      endpoint: document.getElementById("cfg-endpoint").value.trim(),
      instance: document.getElementById("cfg-instance").value.trim(),
    };
    bridge.save_whatsapp_config(JSON.stringify(payload), (result) => {
      const res = JSON.parse(result);
      const status = document.getElementById("whatsapp-save-status");
      status.textContent = res.ok ? "Salvo com sucesso." : `Erro: ${res.error}`;
      setTimeout(() => { status.textContent = ""; }, 3000);
    });
  });
}

function initQuitButton() {
  document.getElementById("btn-quit").addEventListener("click", () => {
    if (confirm("Deseja realmente encerrar a aplicação Kinesis SMA-TR?")) {
      bridge.quit_app();
    }
  });
}

function boolTag(value, label) {
  return `<span class="hud-flag ${value ? "on" : ""}">${escapeHtml(label)}</span>`;
}

function renderHud(data) {
  hud.classList.remove("hidden");
  videoFrame.classList.toggle("alert-fall", !!data.fall_alert);
  videoFrame.classList.toggle("alert-drowsy", !data.fall_alert && !!data.drowsy_alert);

  const gesturesText = Object.entries(data.gestures || {})
    .map(([side, label]) => `${side}: ${label}`).join(", ") || "Nenhum";
  const mobilityText = (data.mobility_aids || []).join(", ") || "Nenhum";
  let idText = escapeHtml(data.person_label || "N/A");
  if (data.person_count > 1) idText += ` (+${data.person_count - 1} no ambiente)`;

  hud.innerHTML = `
    <div class="hud-title">${escapeHtml(data.source_name || "")}</div>
    <div class="hud-grid">
      <div class="hud-row"><span>ID</span><b>${idText}</b></div>
      <div class="hud-row"><span>Expressão</span><b>${escapeHtml(data.emotion || "N/A")}</b></div>
      <div class="hud-row"><span>Cabeça</span><b>${escapeHtml(data.head_pose || "N/A")}</b></div>
      <div class="hud-row"><span>Postura</span><b>${escapeHtml(data.posture || "N/A")}</b></div>
      <div class="hud-row"><span>Movimento</span><b>${escapeHtml(data.motion || "N/A")}</b></div>
      <div class="hud-row"><span>Gestos</span><b>${escapeHtml(gesturesText)}</b></div>
      <div class="hud-row"><span>Dispositivos</span><b>${escapeHtml(mobilityText)}</b></div>
      <div class="hud-flags">
        ${boolTag(data.arm_raised, "Braço levantado")}
        ${boolTag(data.hand_near_face, "Mão no rosto")}
      </div>
    </div>
    ${data.fall_alert ? '<div class="hud-alert">ALERTA: QUEDA DETECTADA!</div>' : ""}
    ${!data.fall_alert && data.drowsy_alert ? '<div class="hud-alert hud-alert-drowsy">ALERTA: SINAL DE SONOLÊNCIA!</div>' : ""}
  `;
}

// --- Camadas sobre o video: caixa delimitadora + rotulo (Tipo/Confianca%)
// + icone de marcador, para pessoas rastreadas (src/vision/person_tracker.py)
// e objetos detectados (src/vision/object_detector.py). Um <svg> com viewBox
// nas dimensoes em pixel do frame original, sobreposto exatamente sobre a
// area renderizada do <canvas> (calculada via getBoundingClientRect, pois o
// canvas pode ter barras de letterbox dentro do video-frame) - assim as
// coordenadas de bbox (em pixels do frame) nao precisam de nenhuma conta de
// escala manual, o proprio SVG cuida disso.
function clearOverlay() {
  overlayMarks.innerHTML = "";
  overlaySvg.hidden = true;
}

function positionOverlaySvg() {
  const stageRect = videoFrame.getBoundingClientRect();
  const canvasRect = canvas.getBoundingClientRect();
  overlaySvg.style.left = (canvasRect.left - stageRect.left) + "px";
  overlaySvg.style.top = (canvasRect.top - stageRect.top) + "px";
  overlaySvg.style.width = `${canvasRect.width}px`;
  overlaySvg.style.height = `${canvasRect.height}px`;
}

function boxMarkup(x1, y1, x2, y2, color, label, iconId) {
  const w = Math.max(1, x2 - x1);
  const h = Math.max(1, y2 - y1);
  const fontSize = Math.max(13, Math.round(h * 0.07));
  const iconSize = Math.max(16, Math.round(Math.min(w, h) * 0.16));
  const labelY = y1 > fontSize + 6 ? y1 - 6 : y2 + fontSize + 4;
  return `
    <rect x="${x1}" y="${y1}" width="${w}" height="${h}" fill="none" stroke="${color}" stroke-width="2.5" rx="4"></rect>
    <use href="#${iconId}" x="${x1 + 3}" y="${y1 + 3}" width="${iconSize}" height="${iconSize}" fill="${color}"></use>
    <text x="${x1 + iconSize + 8}" y="${labelY}" fill="${color}" font-size="${fontSize}" font-weight="600">${escapeHtml(label)}</text>
  `;
}

function drawDetectionOverlay(data) {
  const frameSize = data.frame_size;
  const people = data.people || [];
  const detections = data.detections || [];
  if (!frameSize || (!people.length && !detections.length)) {
    clearOverlay();
    return;
  }
  const [fw, fh] = frameSize;
  overlaySvg.setAttribute("viewBox", `0 0 ${fw} ${fh}`);
  positionOverlaySvg();
  overlaySvg.hidden = false;

  const marks = [];
  people.forEach((p) => {
    const [x1, y1, x2, y2] = p.bbox;
    const color = p.is_primary ? "#facc15" : "#22c55e";
    const label = `Pessoa ${p.track_id}${p.is_primary ? " · principal" : ""} · ${Math.round(p.confidence * 100)}%`;
    marks.push(boxMarkup(x1, y1, x2, y2, color, label, "icon-person"));
  });
  detections.forEach((d) => {
    const [x1, y1, x2, y2] = d.bbox;
    const label = `${d.label} · ${Math.round(d.confidence * 100)}%`;
    marks.push(boxMarkup(x1, y1, x2, y2, "#38bdf8", label, "icon-object"));
  });
  overlayMarks.innerHTML = marks.join("");
}

function prependAlert(event) {
  const feed = document.getElementById("alert-feed");
  const empty = document.getElementById("alert-empty");
  if (empty) empty.remove();
  const card = document.createElement("div");
  card.className = `alert-card ${severityClass(event.severity)}`;
  const when = new Date(event.timestamp).toLocaleString("pt-BR");
  card.innerHTML = `
    <div class="alert-top">
      <span class="alert-name">${event.event_id} · ${escapeHtml(event.name)}</span>
      <span class="sev-pill ${severityClass(event.severity)}">${severityLabel(event.severity)}</span>
    </div>
    <div class="alert-meta">${escapeHtml(event.source_name)}${event.person_label ? " · " + escapeHtml(event.person_label) : ""} · ${when}</div>
    <div class="alert-message">${escapeHtml(event.message)}</div>
  `;
  feed.prepend(card);
  while (feed.children.length > 50) feed.removeChild(feed.lastChild);
  document.getElementById("alert-count").textContent = `${feed.children.length} evento(s)`;
}

window.onload = function () {
  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;

    initTabs();
    initContactModal();
    initWhatsAppForm();
    initQuitButton();

    bridge.get_initial_state(function (result) {
      state = JSON.parse(result);
      renderCameraSelect();
      renderStatusCards();
      renderEventsTable();
      renderContactsTable();
      renderWhatsAppConfig();
      // Historico persistido (SQLite, ver EventLogger): sem isso o feed de
      // alertas comecava vazio a cada abertura da GUI, mesmo com eventos ja
      // registrados em disco de uma execucao anterior. Reordena explicitamente
      // do mais antigo para o mais novo antes de prependar cada um, para que
      // o feed final fique do mais recente (topo) para o mais antigo (base)
      // independente da ordem em que o backend devolveu o histórico.
      (state.event_history || [])
        .slice()
        .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp))
        .forEach(prependAlert);
    });

    bridge.frameReady.connect(function (sourceName, jpegBase64) {
      placeholder.classList.add("hidden");
      videoImg.onload = function () {
        canvas.width = videoImg.naturalWidth;
        canvas.height = videoImg.naturalHeight;
        ctx.drawImage(videoImg, 0, 0);
      };
      videoImg.src = "data:image/jpeg;base64," + jpegBase64;
    });

    bridge.metricsUpdated.connect(function (json) {
      const data = JSON.parse(json);
      document.getElementById("fps-badge").textContent = `${data.fps} FPS`;
      renderHud(data);
      drawDetectionOverlay(data);
    });

    bridge.eventLogged.connect(function (json) {
      prependAlert(JSON.parse(json));
    });

    bridge.statusChanged.connect(function (json) {
      if (!state) return;
      state.status = JSON.parse(json);
      renderStatusCards();
    });
  });
};
