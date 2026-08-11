const docsBody = document.getElementById("docs-body");
const uploadForm = document.getElementById("upload-form");
const uploadFile = document.getElementById("upload-file");
const uploadScenario = document.getElementById("upload-scenario");
const uploadBtn = document.getElementById("upload-btn");
const uploadStatus = document.getElementById("upload-status");
const callsList = document.getElementById("calls-list");
const alertsBanner = document.getElementById("alerts-banner");
const alertsList = document.getElementById("alerts-list");
const callDialog = document.getElementById("call-dialog");
const dialogTitle = document.getElementById("dialog-title");
const dialogBody = document.getElementById("dialog-body");

function esc(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

function shortId(id) {
  const s = String(id || "");
  return s.length > 12 ? s.slice(0, 12) + "…" : s;
}

function formatDate(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("es", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function badgeClass(criticidad) {
  const c = String(criticidad || "").toLowerCase();
  if (c === "rojo" || c === "amarillo" || c === "verde") return c;
  return "verde";
}

async function loadDocuments() {
  try {
    const res = await fetch("/api/documents");
    const data = await res.json();
    const docs = data.documents || [];
    if (docs.length === 0) {
      docsBody.innerHTML =
        '<tr><td colspan="5" class="muted">No hay documentos en la base de conocimiento.</td></tr>';
      return;
    }
    docsBody.innerHTML = docs
      .map(
        (d) => `
      <tr>
        <td>${esc(d.source)}</td>
        <td>${esc(d.scenario)}</td>
        <td>${esc(d.n_chunks)}</td>
        <td><span class="chip verde">procesado y disponible</span></td>
        <td><button type="button" class="btn-danger" data-doc-id="${esc(d.doc_id)}" data-doc-name="${esc(d.source)}">Eliminar</button></td>
      </tr>`
      )
      .join("");
  } catch (err) {
    docsBody.innerHTML =
      '<tr><td colspan="5" class="error">Error al cargar documentos.</td></tr>';
  }
}

docsBody.addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-doc-id]");
  if (!btn) return;
  const docId = btn.dataset.docId;
  const name = btn.dataset.docName || docId;
  if (!confirm(`¿Eliminar el documento "${name}" de la base de conocimiento?`)) return;
  btn.disabled = true;
  try {
    const res = await fetch(`/api/documents/${encodeURIComponent(docId)}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      alert(data.detail || "No se pudo eliminar el documento.");
    }
  } catch (err) {
    alert("Error de red al eliminar el documento.");
  }
  loadDocuments();
});

uploadForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const file = uploadFile.files[0];
  if (!file) return;
  const scenario = uploadScenario.value.trim() || "subido_en_vivo";
  uploadBtn.disabled = true;
  uploadStatus.innerHTML = '<span class="muted">Procesando…</span>';
  const form = new FormData();
  form.append("file", file);
  try {
    const res = await fetch(
      `/api/documents?scenario=${encodeURIComponent(scenario)}`,
      { method: "POST", body: form }
    );
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      uploadStatus.innerHTML = '<span class="chip verde">procesado y disponible</span>';
      uploadForm.reset();
      uploadScenario.value = "subido_en_vivo";
      loadDocuments();
    } else {
      uploadStatus.innerHTML = `<span class="error">${esc(data.detail || "Error al procesar el documento.")}</span>`;
    }
  } catch (err) {
    uploadStatus.innerHTML = '<span class="error">Error de red al subir el documento.</span>';
  }
  uploadBtn.disabled = false;
});

function renderAlerts(alerts) {
  if (!alerts || alerts.length === 0) {
    alertsBanner.classList.add("hidden");
    alertsList.innerHTML = "";
    return;
  }
  alertsBanner.classList.remove("hidden");
  alertsList.innerHTML = alerts
    .map((a) => {
      const flags = (a.red_flags || []).map(esc).join(", ");
      return `<li><code>${esc(shortId(a.conversation_id))}</code>${flags ? " — " + flags : ""}</li>`;
    })
    .join("");
}

function renderCalls(calls) {
  if (!calls || calls.length === 0) {
    callsList.innerHTML = '<p class="muted">Aún no hay llamadas registradas.</p>';
    return;
  }
  callsList.innerHTML = calls
    .map((c) => {
      const crit = badgeClass(c.criticidad_final);
      const quien = [c.paciente, c.procedimiento].filter(Boolean).map(esc).join(" — ");
      return `
      <article class="call-card">
        <div class="call-head">
          <code>${esc(shortId(c.conversation_id))}</code>
          <span class="badge ${crit}">${esc(c.criticidad_final || "verde")}</span>
          ${c.escalar ? '<span class="escalada">⚠ ESCALADA</span>' : ""}
        </div>
        ${quien ? `<div class="call-quien">${quien}</div>` : ""}
        <div class="call-meta">${esc(formatDate(c.ts))} · ${esc(c.n_turnos ?? "?")} turnos</div>
        <p class="call-summary">${esc(c.resumen_narrativo || "Sin resumen disponible.")}</p>
        <button type="button" data-call-id="${esc(c.conversation_id)}">Ver detalle</button>
      </article>`;
    })
    .join("");
}

async function loadCalls() {
  try {
    const res = await fetch("/api/calls");
    const data = await res.json();
    renderAlerts(data.alerts || []);
    renderCalls(data.calls || []);
  } catch (err) {
    callsList.innerHTML = '<p class="error">Error al cargar las llamadas.</p>';
  }
}

function renderTurn(t) {
  const refs = (t.rag_sources || [])
    .map((r) => {
      const page = r.page != null ? `, pág. ${esc(r.page)}` : "";
      return `<li>${esc(r.source)}${page} <span class="muted">(${esc(r.scenario)})</span></li>`;
    })
    .join("");
  return `
    <div class="turn">
      ${t.user_text ? `<p class="turn-patient"><strong>Paciente:</strong> ${esc(t.user_text)}</p>` : ""}
      <p class="turn-agent"><strong>Agente:</strong> ${esc(t.spoken_text || "")}</p>
      ${refs ? `<details class="turn-refs"><summary>Referencias usadas</summary><ul>${refs}</ul></details>` : ""}
    </div>`;
}

function renderSummary(id, s) {
  if (!s) return "";
  const crit = badgeClass(s.criticidad_final);
  const sintomas = (s.sintomas_reportados || []).map(esc).join(", ") || "—";
  const flags = (s.red_flags || []).map(esc).join(", ");
  const refs = (s.referencias || [])
    .map((r) => {
      const page = r.page != null ? `, pág. ${esc(r.page)}` : "";
      return `<li>${esc(r.source)}${page} <span class="muted">(${esc(r.scenario)})</span></li>`;
    })
    .join("");
  return `
    <section class="call-summary-card">
      <div class="call-head">
        <span class="badge ${crit}">${esc(s.criticidad_final || "verde")}</span>
        ${s.escalar ? '<span class="escalada">⚠ ESCALADA AL EQUIPO CLÍNICO</span>' : ""}
      </div>
      <dl class="summary-grid">
        <dt>Paciente</dt><dd>${esc(s.paciente || "no identificado")}</dd>
        <dt>Procedimiento</dt><dd>${esc(s.procedimiento || "—")}</dd>
        <dt>Síntomas reportados</dt><dd>${sintomas}</dd>
        ${flags ? `<dt>Señales de alarma</dt><dd>${flags}</dd>` : ""}
        <dt>Próximos pasos</dt><dd>${esc(s.proximos_pasos || "—")}</dd>
      </dl>
      <p class="call-summary-narrativa">${esc(s.resumen_narrativo || "Sin resumen disponible.")}</p>
      ${refs ? `<details class="turn-refs"><summary>Referencias citadas en la llamada</summary><ul>${refs}</ul></details>` : ""}
      <div class="dialog-actions">
        <button type="button" data-regen-id="${esc(id)}">Regenerar resumen</button>
        <button type="button" class="btn-danger" data-delete-id="${esc(id)}">Eliminar registro</button>
      </div>
    </section>`;
}

async function showCallDetail(id) {
  dialogTitle.textContent = `Detalle de llamada ${shortId(id)}`;
  dialogBody.innerHTML = '<p class="muted">Cargando…</p>';
  callDialog.showModal();
  try {
    const res = await fetch(`/api/calls/${encodeURIComponent(id)}`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      dialogBody.innerHTML = `<p class="error">${esc(data.detail || "No se pudo cargar el detalle.")}</p>`;
      return;
    }
    const data = await res.json();
    const turns = data.turns || data.turnos || [];
    const summaryHtml = renderSummary(id, data.summary || data.resumen);
    const turnsHtml = turns.length
      ? `<h4 class="turns-heading">Transcripción</h4>${turns.map(renderTurn).join("")}`
      : '<p class="muted">Sin turnos registrados.</p>';
    dialogBody.innerHTML = summaryHtml + turnsHtml;
  } catch (err) {
    dialogBody.innerHTML = '<p class="error">Error de red al cargar el detalle.</p>';
  }
}

callsList.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-call-id]");
  if (btn) showCallDetail(btn.dataset.callId);
});

dialogBody.addEventListener("click", async (ev) => {
  const regenBtn = ev.target.closest("button[data-regen-id]");
  if (regenBtn) {
    const id = regenBtn.dataset.regenId;
    regenBtn.disabled = true;
    regenBtn.textContent = "Regenerando…";
    try {
      const res = await fetch(`/api/calls/${encodeURIComponent(id)}/summarize`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "No se pudo regenerar el resumen.");
      } else {
        await showCallDetail(id);
        loadCalls();
        return;
      }
    } catch (err) {
      alert("Error de red al regenerar el resumen.");
    }
    regenBtn.disabled = false;
    regenBtn.textContent = "Regenerar resumen";
    return;
  }

  const delBtn = ev.target.closest("button[data-delete-id]");
  if (delBtn) {
    const id = delBtn.dataset.deleteId;
    if (!confirm(`¿Eliminar TODO el registro de la llamada "${shortId(id)}"? Esta acción no se puede deshacer.`)) return;
    delBtn.disabled = true;
    try {
      const res = await fetch(`/api/calls/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "No se pudo eliminar el registro.");
        delBtn.disabled = false;
        return;
      }
      callDialog.close();
      loadCalls();
    } catch (err) {
      alert("Error de red al eliminar el registro.");
      delBtn.disabled = false;
    }
  }
});

document.getElementById("dialog-close").addEventListener("click", () => {
  callDialog.close();
});

loadDocuments();
loadCalls();
setInterval(loadCalls, 10000);
