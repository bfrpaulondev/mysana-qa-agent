const state = {
  busy: false,
};

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function toast(message) {
  const element = $("toast");
  element.textContent = message;
  element.classList.remove("hidden");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => element.classList.add("hidden"), 4200);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || `HTTP ${response.status}`);
  }
  return payload;
}

function renderProviders(providers) {
  $("providersGrid").innerHTML = providers.map((provider) => {
    let statusText = "Não configurado";
    let dotClass = "";
    let badgeClass = "badge-neutral";

    if (provider.paid && !provider.enabled) {
      statusText = provider.configured ? "Desactivado" : "Desactivado";
      dotClass = "warn";
      badgeClass = "badge-warn";
    } else if (provider.configured && provider.enabled) {
      statusText = provider.last_ok === false ? "Erro no último teste" : "Pronto";
      dotClass = provider.last_ok === false ? "warn" : "ok";
      badgeClass = provider.last_ok === false ? "badge-warn" : "badge-pass";
    }

    const latency = provider.latency_ms == null ? "—" : `${provider.latency_ms} ms`;

    return `
      <article class="provider-card">
        <div class="provider-top">
          <div style="display:flex;align-items:center;gap:10px;min-width:0">
            <span class="status-dot ${dotClass}"></span>
            <span class="provider-name">${escapeHtml(provider.name)}</span>
          </div>
          <span class="badge ${badgeClass}">${statusText}</span>
        </div>
        <span class="provider-model" title="${escapeHtml(provider.model)}">${escapeHtml(provider.model)}</span>
        <div class="provider-meta">
          <span>${provider.paid ? "fallback pago" : "free tier"}</span>
          <span>${latency}</span>
        </div>
      </article>
    `;
  }).join("");
}

function renderSession(session) {
  const open = session.state === "open";
  const error = session.state === "error";
  const badge = $("sessionBadge");

  badge.className = `badge ${open ? "badge-pass" : error ? "badge-fail" : "badge-neutral"}`;
  badge.textContent = open ? "Aberta" : error ? "Erro" : "Fechada";

  $("sessionTitle").textContent = session.title || session.message || "Chrome QA ainda não foi aberto";
  $("sessionUrl").textContent = session.url || "mysana.sanahotels.com";
  $("startTestButton").disabled = !open || state.busy;
  $("closeSessionButton").disabled = !open || state.busy;
  $("openSessionButton").disabled = state.busy;
}

function renderLastTest(test) {
  if (!test) {
    $("emptyExecution").classList.remove("hidden");
    $("testResult").classList.add("hidden");
    return;
  }

  $("emptyExecution").classList.add("hidden");
  $("testResult").classList.remove("hidden");
  $("resultTitle").textContent = test.title || test.url || "—";
  $("resultElements").textContent = test.interactive_elements ?? "—";
  $("resultReport").textContent = test.report_dir || "—";

  const badge = $("testBadge");
  badge.textContent = test.status || "—";
  badge.className = `badge ${test.ok ? "badge-pass" : "badge-fail"}`;
}

async function refresh() {
  try {
    const status = await request("/api/status");
    renderProviders(status.providers);
    renderSession(status.session);
    renderLastTest(status.last_test);

    $("safetyBadge").textContent = status.safe_mode ? "Modo seguro" : "Acções perigosas permitidas";
    $("safetyBadge").className = `badge ${status.safe_mode ? "badge-safe" : "badge-fail"}`;
    $("refreshState").textContent = "Actualizado agora";
  } catch (error) {
    $("refreshState").textContent = "Backend indisponível";
  }
}

async function withBusy(button, action) {
  if (state.busy) return;
  state.busy = true;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "A executar…";
  try {
    await action();
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
    button.textContent = previous;
    await refresh();
  }
}

$("testProvidersButton").addEventListener("click", () => withBusy($("testProvidersButton"), async () => {
  const payload = await request("/api/providers/test", { method: "POST" });
  const passed = payload.results.filter((item) => item.ok).length;
  toast(`Providers testados: ${passed}/${payload.results.length} disponíveis.`);
}));

$("openSessionButton").addEventListener("click", () => withBusy($("openSessionButton"), async () => {
  await request("/api/session/open", { method: "POST" });
  toast("Chrome QA aberto. Faz login no MySANA na janela separada.");
}));

$("closeSessionButton").addEventListener("click", () => withBusy($("closeSessionButton"), async () => {
  await request("/api/session/close", { method: "POST" });
  toast("Sessão Chrome QA fechada.");
}));

$("startTestButton").addEventListener("click", () => withBusy($("startTestButton"), async () => {
  const result = await request("/api/tests/start", { method: "POST" });
  toast(result.message);
  renderLastTest(result);
}));

refresh();
window.setInterval(refresh, 3000);
