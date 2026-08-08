import { Conversation } from "https://esm.sh/@elevenlabs/client";

const callBtn = document.getElementById("call-btn");
const statusEl = document.getElementById("call-status");
const transcriptEl = document.getElementById("transcript");
const errorEl = document.getElementById("call-error");

const STATUS_LABELS = {
  disconnected: "Desconectado",
  connecting: "Conectando…",
  listening: "El agente escucha",
  speaking: "El agente habla",
};

let conversation = null;
let starting = false;

function setStatus(state) {
  statusEl.textContent = STATUS_LABELS[state];
  statusEl.dataset.state = state;
}

function setButton(inCall) {
  callBtn.textContent = inCall ? "Colgar" : "📞 Iniciar llamada";
  callBtn.classList.toggle("in-call", inCall);
}

function showError(message) {
  errorEl.textContent = message || "";
  errorEl.hidden = !message;
}

function addBubble(source, text) {
  if (!text) return;
  const empty = document.getElementById("transcript-empty");
  if (empty) empty.remove();
  const row = document.createElement("div");
  row.className = "bubble-row " + (source === "ai" ? "agent" : "patient");
  const label = document.createElement("div");
  label.className = "bubble-label";
  label.textContent = source === "ai" ? "Clara (agente)" : "Paciente";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  row.appendChild(label);
  row.appendChild(bubble);
  transcriptEl.appendChild(row);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function handleDisconnect() {
  const conv = conversation;
  conversation = null;
  setStatus("disconnected");
  setButton(false);
  try {
    const conversationId = conv && conv.getId ? conv.getId() : null;
    if (conversationId) {
      fetch(`/api/calls/${encodeURIComponent(conversationId)}/summarize`, { method: "POST" }).catch(() => {});
    }
  } catch (_) {
    /* silencioso */
  }
}

async function startCall() {
  showError("");
  setStatus("connecting");
  starting = true;
  callBtn.disabled = true;
  try {
    await navigator.mediaDevices.getUserMedia({ audio: true });

    const resp = await fetch("/api/voice/signed-url");
    if (!resp.ok) {
      let detail = `No se pudo obtener la URL firmada (HTTP ${resp.status}).`;
      try {
        const data = await resp.json();
        if (data.detail) detail = data.detail;
      } catch (_) {
        /* respuesta sin JSON */
      }
      throw new Error(detail);
    }
    const { signed_url: signedUrl } = await resp.json();

    conversation = await Conversation.startSession({
      signedUrl,
      onConnect: () => {
        setStatus("listening");
        setButton(true);
      },
      onDisconnect: () => {
        handleDisconnect();
      },
      onError: (error) => {
        const msg = error && error.message ? error.message : String(error);
        showError("Error en la llamada: " + msg);
      },
      onModeChange: (mode) => {
        setStatus(mode.mode === "speaking" ? "speaking" : "listening");
      },
      onMessage: ({ message, source }) => {
        addBubble(source, message);
      },
    });
  } catch (err) {
    conversation = null;
    setStatus("disconnected");
    setButton(false);
    if (err && (err.name === "NotAllowedError" || err.name === "PermissionDeniedError")) {
      showError("Permiso de micrófono denegado. Habilite el micrófono en el navegador e intente de nuevo.");
    } else {
      showError((err && err.message) || "No se pudo iniciar la llamada.");
    }
  } finally {
    starting = false;
    callBtn.disabled = false;
  }
}

async function endCall() {
  if (!conversation) return;
  callBtn.disabled = true;
  try {
    await conversation.endSession();
  } catch (_) {
    /* silencioso */
  }
  callBtn.disabled = false;
  if (conversation) handleDisconnect();
}

callBtn.addEventListener("click", () => {
  if (starting) return;
  if (conversation) {
    endCall();
  } else {
    startCall();
  }
});
