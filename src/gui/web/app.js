let bridge = null;
let state = null;

const videoImg = new Image();
const canvas = document.getElementById("video-canvas");
const ctx = canvas.getContext("2d");
const placeholder = document.getElementById("video-placeholder");

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function severityClass(sev) {
  return `sev-${(sev || "info").toLowerCase()}`;
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
  select.onchange = () => bridge.select_camera(parseInt(select.value, 10));
}

function statusCard(label, dotClass, text) {
  const div = document.createElement("div");
  div.className = "status-card";
  div.innerHTML = `<span class="status-dot ${dotClass}"></span>` +
    `<div><div class="font-medium">${escapeHtml(label)}</div>` +
    `<div class="text-slate-500">${escapeHtml(text)}</div></div>`;
  return div;
}

function renderStatusCards() {
  const container = document.getElementById("status-cards");
  container.innerHTML = "";
  state.status.cameras.forEach((cam) => {
    container.appendChild(statusCard(cam.name, cam.connected ? "on" : "off", cam.connected ? "Conectado" : "Sem sinal"));
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
    tr.className = "border-t border-slate-800" + (ev.implemented ? "" : " opacity-40");
    const contactsOptions = state.contacts.map((c) =>
      `<option value="${c.id}" ${ev.notify_contact_ids.includes(c.id) ? "selected" : ""}>${escapeHtml(c.name)}</option>`
    ).join("");
    tr.innerHTML = `
      <td class="px-4 py-2">
        <div class="font-medium">${ev.id}</div>
        <div class="text-slate-400 text-xs">${escapeHtml(ev.name)}</div>
      </td>
      <td class="px-4 py-2 text-slate-400">${escapeHtml(ev.category)}</td>
      <td class="px-4 py-2"><span class="sev-pill ${severityClass(ev.severity)}">${ev.severity}</span></td>
      <td class="px-4 py-2"><input type="checkbox" class="toggle" ${ev.enabled ? "checked" : ""} ${ev.implemented ? "" : "disabled"} data-event="${ev.id}" /></td>
      <td class="px-4 py-2"><select multiple class="field-input h-16 text-xs" data-event-contacts="${ev.id}" ${ev.implemented ? "" : "disabled"}>${contactsOptions}</select></td>
      <td class="px-4 py-2"><button class="btn-secondary" data-test-event="${ev.id}">Testar</button></td>
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
    tr.className = "border-t border-slate-800";
    tr.innerHTML = `
      <td class="px-4 py-2">${escapeHtml(c.name)}</td>
      <td class="px-4 py-2 text-slate-400">${escapeHtml(c.relationship || "-")}</td>
      <td class="px-4 py-2 text-slate-400">${escapeHtml(c.whatsapp_number || "-")}</td>
      <td class="px-4 py-2 text-right"><button class="btn-secondary" data-delete-contact="${c.id}">Remover</button></td>
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
  document.getElementById("cfg-device-id").value = state.whatsapp.device_id || "";
  document.getElementById("cfg-endpoint").value = state.whatsapp.endpoint || "";
  document.getElementById("cfg-token").value = state.whatsapp.token || "";
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
      device_id: document.getElementById("cfg-device-id").value.trim(),
      endpoint: document.getElementById("cfg-endpoint").value.trim(),
      token: document.getElementById("cfg-token").value.trim(),
    };
    bridge.save_whatsapp_config(JSON.stringify(payload), (result) => {
      const res = JSON.parse(result);
      const status = document.getElementById("whatsapp-save-status");
      status.textContent = res.ok ? "Salvo com sucesso." : `Erro: ${res.error}`;
      setTimeout(() => { status.textContent = ""; }, 3000);
    });
  });
}

function prependAlert(event) {
  const feed = document.getElementById("alert-feed");
  const card = document.createElement("div");
  card.className = `alert-card ${severityClass(event.severity)}`;
  const time = new Date(event.timestamp).toLocaleTimeString("pt-BR");
  card.innerHTML = `
    <div class="flex items-center justify-between gap-2">
      <span class="font-medium">${event.event_id} · ${escapeHtml(event.name)}</span>
      <span class="sev-pill ${severityClass(event.severity)}">${event.severity}</span>
    </div>
    <div class="text-slate-400 text-xs mt-1">${escapeHtml(event.source_name)}${event.person_label ? " · " + escapeHtml(event.person_label) : ""} · ${time}</div>
    <div class="text-slate-300 text-xs mt-1">${escapeHtml(event.message)}</div>
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

    bridge.get_initial_state(function (result) {
      state = JSON.parse(result);
      renderCameraSelect();
      renderStatusCards();
      renderEventsTable();
      renderContactsTable();
      renderWhatsAppConfig();
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
