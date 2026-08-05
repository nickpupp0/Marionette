const socket = io();

const chatLog = document.getElementById("chatLog");
const eventLog = document.getElementById("eventLog");
const defenseSwitch = document.getElementById("defenseSwitch");
const defenseLabel = document.getElementById("defenseLabel");

function addChatMsg(who, text) {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.innerHTML = `<div class="who">${who === "user" ? "you" : "agent"}</div><div class="body"></div>`;
  div.querySelector(".body").textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function addEvent(kind, title, payload) {
  const div = document.createElement("div");
  div.className = `ev ${kind}`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(payload, null, 2);
  div.innerHTML = `<div class="ev-title">${title}</div>`;
  div.appendChild(pre);
  eventLog.appendChild(div);
  eventLog.scrollTop = eventLog.scrollHeight;
}

// --- chat ---
document.getElementById("chatForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = document.getElementById("chatInput");
  const message = input.value.trim();
  if (!message) return;
  addChatMsg("user", message);
  input.value = "";

  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  const data = await res.json();
  addChatMsg("bot", data.answer);
  refreshPending();
});

// --- model selector ---
document.getElementById("modelSelect").addEventListener("change", async (e) => {
  await fetch("/api/admin/model", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model: e.target.value }),
  });
});

socket.on("model_changed", (data) => {
  document.getElementById("modelSelect").value = data.model;
  addEvent("ingest", `model switched to ${data.model}`, data);
});

// --- defense toggle ---
defenseSwitch.addEventListener("change", async () => {
  await fetch("/api/admin/defense_mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: defenseSwitch.checked }),
  });
});

function applyDefenseModeDisplay(enabled) {
  defenseSwitch.checked = enabled;
  defenseLabel.textContent = enabled ? "DEFENDED" : "VULNERABLE";
  defenseLabel.className = `mode-label ${enabled ? "mode-on" : "mode-off"}`;
}

socket.on("defense_mode", (data) => applyDefenseModeDisplay(data.enabled));

// --- sync UI to actual server state ---
// Runs on initial load AND on every socket (re)connect, specifically to
// catch the case where the server restarted (e.g. Flask's debug-mode
// auto-reloader firing on a file save) while this browser tab stayed
// open -- without this, the dropdown/toggle would keep showing
// whatever was selected before the restart even though the server's
// actual state had already reverted to defaults underneath it.
async function syncState() {
  const res = await fetch("/api/admin/state");
  const data = await res.json();
  document.getElementById("modelSelect").value = data.model_key;
  applyDefenseModeDisplay(data.defense_mode);
}

socket.on("connect", syncState);

// --- tabs ---
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach(c => c.classList.add("hidden"));
    btn.classList.add("active");
    document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
    if (btn.dataset.tab === "pending") refreshPending();
    if (btn.dataset.tab === "env") refreshEnv();
  });
});

// --- plant email / webpage ---
document.getElementById("plantEmailBtn").addEventListener("click", async () => {
  const sender = document.getElementById("emailSender").value.trim();
  const subject = document.getElementById("emailSubject").value.trim();
  const body = document.getElementById("emailBody").value.trim();
  if (!sender || !subject || !body) return;
  await fetch("/api/admin/plant_email", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sender, subject, body }),
  });
  document.getElementById("emailSender").value = "";
  document.getElementById("emailSubject").value = "";
  document.getElementById("emailBody").value = "";
});

document.getElementById("plantWebBtn").addEventListener("click", async () => {
  const url = document.getElementById("webUrl").value.trim();
  const content = document.getElementById("webContent").value.trim();
  if (!url || !content) return;
  await fetch("/api/admin/plant_webpage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, content }),
  });
  document.getElementById("webUrl").value = "";
  document.getElementById("webContent").value = "";
});

document.getElementById("plantEventBtn").addEventListener("click", async () => {
  const date = document.getElementById("eventDate").value.trim();
  const title = document.getElementById("eventTitle").value.trim();
  const description = document.getElementById("eventDescription").value.trim();
  const attendeesRaw = document.getElementById("eventAttendees").value.trim();
  const attendees = attendeesRaw ? attendeesRaw.split(",").map(s => s.trim()).filter(Boolean) : [];
  if (!date || !title || !description) return;
  await fetch("/api/admin/plant_calendar_event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ date, title, description, attendees }),
  });
  document.getElementById("eventDate").value = "";
  document.getElementById("eventTitle").value = "";
  document.getElementById("eventDescription").value = "";
  document.getElementById("eventAttendees").value = "";
});

// --- attacks ---
document.querySelectorAll(".attack-btn").forEach(btn => {
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Running...";
    await fetch(`/api/attacks/run/${btn.dataset.attack}`, { method: "POST" });
    setTimeout(() => { btn.disabled = false; btn.textContent = original; refreshPending(); }, 4000);
  });
});

socket.on("attack_log", (data) => {
  addEvent("blocked", `attack script output: ${data.name}`, { output: data.output.split("\n") });
});

document.getElementById("resetBtn").addEventListener("click", async () => {
  await fetch("/api/admin/reset", { method: "POST" });
  chatLog.innerHTML = "";
  eventLog.innerHTML = "";
  refreshPending();
  refreshEnv();
});

// --- pending confirmations ---
async function refreshPending() {
  const res = await fetch("/api/pending");
  const pending = await res.json();
  const list = document.getElementById("pendingList");
  const stillPending = pending.filter(p => p.status === "pending");
  list.innerHTML = stillPending.map(p => `
    <div class="pending-card" data-id="${p.id}">
      <div class="pending-title">${p.tool} — awaiting confirmation</div>
      <pre>${JSON.stringify(p.input, null, 2)}</pre>
      <div class="pending-actions">
        <button class="approve" data-id="${p.id}" data-approve="true">Approve</button>
        <button class="deny" data-id="${p.id}" data-approve="false">Deny</button>
      </div>
    </div>
  `).join("") || "<p class='hint'>No pending actions.</p>";

  list.querySelectorAll("button[data-approve]").forEach(btn => {
    btn.addEventListener("click", async () => {
      await fetch(`/api/pending/${btn.dataset.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve: btn.dataset.approve === "true" }),
      });
      refreshPending();
    });
  });
}

socket.on("pending_confirmation", () => refreshPending());
socket.on("pending_resolved", (data) => {
  addEvent("pending_confirmation", `action ${data.action_id} ${data.approved ? "approved" : "denied"}`, data);
  refreshPending();
});

// --- environment browser ---
let currentEnvTab = "inbox";
document.querySelectorAll(".env-tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".env-tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentEnvTab = btn.dataset.env;
    refreshEnv();
  });
});

async function refreshEnv() {
  const res = await fetch(`/api/${currentEnvTab}`);
  const items = await res.json();
  const list = document.getElementById("envList");

  if (currentEnvTab === "inbox") {
    list.innerHTML = items.map(e => `
      <div class="kb-doc">
        <div class="kb-title">${e.subject}</div>
        <div class="kb-meta">from=${e.sender} · <span class="trust-${e.trust_level}">${e.trust_level}</span> · id=${e.id}</div>
      </div>`).join("") || "<p class='hint'>Empty.</p>";
  } else if (currentEnvTab === "web") {
    list.innerHTML = items.map(w => `
      <div class="kb-doc">
        <div class="kb-title">${w.url}</div>
        <div class="kb-meta"><span class="trust-${w.trust_level}">${w.trust_level}</span></div>
      </div>`).join("") || "<p class='hint'>Empty.</p>";
  } else if (currentEnvTab === "calendar") {
    list.innerHTML = items.map(c => `
      <div class="kb-doc">
        <div class="kb-title">${c.title}</div>
        <div class="kb-meta">${c.date} · <span class="trust-${c.trust_level}">${c.trust_level}</span> · attendees: ${c.attendees.join(", ") || "none"}</div>
        <div class="kb-meta">${c.description}</div>
      </div>`).join("") || "<p class='hint'>Empty.</p>";
  } else if (currentEnvTab === "sent") {
    list.innerHTML = items.map(s => `
      <div class="kb-doc">
        <div class="kb-title">${s.subject}</div>
        <div class="kb-meta">to=${s.to} ${s.bcc ? `bcc=${s.bcc}` : ""} ${s.flagged_attacker_recipient ? "<span class='trust-unverified'>[ATTACKER RECIPIENT]</span>" : ""}</div>
      </div>`).join("") || "<p class='hint'>No emails sent yet.</p>";
  }
}

// --- live tool-call log ---
socket.on("tool_call", (data) => addEvent(data.blocked_pending_confirmation ? "pending_confirmation" : "tool_call", `tool call: ${data.name}`, data));
socket.on("final_answer", (data) => addEvent("final_answer", "final answer", data));
socket.on("max_steps_reached", (data) => addEvent("blocked", "max steps reached", data));
socket.on("ingest", (data) => addEvent("ingest", `planted ${data.kind}`, data));
socket.on("reset", () => addEvent("ingest", "environment reset", {}));

// initial load
syncState();
refreshEnv();
refreshPending();
