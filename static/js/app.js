function fmt(v) {
  const n = Number(v) || 0;
  return n.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function fmtDate(s) {
  if (!s) return "";
  const parts = String(s).slice(0, 10).split("-");
  if (parts.length !== 3) return s;
  return `${parts[2]}/${parts[1]}/${parts[0]}`;
}

function monthName(ym) {
  if (!ym) return "";
  const names = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
  const [y, m] = ym.split("-");
  return `${names[Number(m) - 1]}/${y}`;
}

function todayISO() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function currentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "Erro na requisição");
    err.status = res.status;
    throw err;
  }
  return data;
}

async function delWithForce(url, msg) {
  try {
    await api(url, { method: "DELETE" });
  } catch (err) {
    if (err.status === 409) {
      const ok = window.confirm(
        (msg || "Este item tem dependências vinculadas.") +
        "\n\nExcluir mesmo assim? (as dependências serão desvinculadas/removidas)"
      );
      if (!ok) throw err;
      await api(url + "?force=1", { method: "DELETE" });
      return;
    }
    throw err;
  }
}

function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(el._t);
  el._t = setTimeout(() => (el.className = "toast"), 2600);
}

async function loadStorageBadge() {
  try {
    const meta = await api("/api/meta");
    const el = document.getElementById("storage-badge");
    if (el) {
      el.textContent = meta.backend === "firestore" ? "☁️ Firebase" : "💾 Arquivo local";
      if (meta.storage_error) {
        el.title = "Erro no Firebase, usando arquivo local: " + meta.storage_error;
      }
    }
  } catch (_) {}
}

function openModal(id) { document.getElementById(id).classList.add("open"); }
function closeModal(id) { document.getElementById(id).classList.remove("open"); }

// ---- Reajuste genérico ----
// fields: [{field, label, value}]
let _adjust = { entity_type: "", entity_id: "", fields: [] };

function openAdjust(entityType, entityId, entityName, fields) {
  _adjust = { entity_type: entityType, entity_id: entityId, fields };
  document.getElementById("adjust-entity").textContent = entityName;
  const sel = document.getElementById("adjust-field");
  sel.innerHTML = fields.map((f, i) => `<option value="${i}">${f.label}</option>`).join("");
  onAdjustFieldChange();
  document.getElementById("adjust-note").value = "";
  openModal("adjust-modal");
}

function onAdjustFieldChange() {
  const i = Number(document.getElementById("adjust-field").value || 0);
  const f = _adjust.fields[i];
  if (f) document.getElementById("adjust-value").value = f.value;
}

async function saveAdjust() {
  const i = Number(document.getElementById("adjust-field").value || 0);
  const f = _adjust.fields[i];
  const value = Number(document.getElementById("adjust-value").value);
  const note = document.getElementById("adjust-note").value;
  try {
    const adj = await api("/api/adjustments", {
      method: "POST",
      body: {
        entity_type: _adjust.entity_type,
        entity_id: _adjust.entity_id,
        field: f.field,
        new_value: value,
        note,
      },
    });
    closeModal("adjust-modal");
    toast(`🔧 ${f.label} de ${fmt(adj.old_value)} para ${fmt(adj.new_value)}`);
    if (typeof load === "function") load();
  } catch (err) {
    toast(err.message, true);
  }
}

function confirmDelete(msg) {
  return window.confirm(msg || "Excluir este item?");
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function highlightActiveNav() {
  const path = location.pathname;
  document.querySelectorAll(".topnav a").forEach((a) => {
    const href = a.getAttribute("href");
    if (href === path) a.classList.add("active");
    if (href === "/" && path === "/") a.classList.add("active");
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadStorageBadge();
  highlightActiveNav();
  document.querySelectorAll(".modal-backdrop").forEach((m) => {
    m.addEventListener("click", (e) => {
      if (e.target === m) m.classList.remove("open");
    });
  });
});
