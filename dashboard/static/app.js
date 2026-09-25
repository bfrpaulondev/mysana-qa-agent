const state = {
  busy: false,
  lastActivityId: null,
  loginModalSuppressed: false,
  approvalBusy: false,
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
      statusText = "Desactivado";
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

function setLoginModal(visible) {
  $("loginModal").classList.toggle("hidden", !visible);
  if (visible) {
    window.setTimeout(() => $("loginUsername").focus(), 50);
  }
}

function renderSession(session, testRunning) {
  const open = session.state === "open";
  const error = session.state === "error";
  const badge = $("sessionBadge");

  badge.className = `badge ${open ? "badge-pass" : error ? "badge-fail" : "badge-neutral"}`;
  badge.textContent = open ? (session.login_required ? "Login necessário" : "Aberta") : error ? "Erro" : "Fechada";

  $("sessionTitle").textContent = session.title || session.message || "Chromium QA ainda não foi aberto";
  $("sessionUrl").textContent = session.url || "mysana.sanahotels.com";
  $("browserName").textContent = session.browser || "Chromium";
  $("startTestButton").disabled = !open || session.login_required || state.busy || testRunning;
  $("closeSessionButton").disabled = !open || state.busy || testRunning;
  $("openSessionButton").disabled = state.busy || testRunning;

  if (session.login_required && !state.loginModalSuppressed && !state.busy) {
    setLoginModal(true);
  } else if (!session.login_required) {
    setLoginModal(false);
    state.loginModalSuppressed = false;
    $("loginPassword").value = "";
  }
}

function renderApproval(approval) {
  const panel = $("approvalPanel");
  if (!approval) {
    panel.classList.add("hidden");
    state.approvalBusy = false;
    return;
  }

  panel.classList.remove("hidden");

  const actionNames = {
    click: "CLICAR",
    write: "ESCREVER",
    select: "SELECCIONAR",
    keypress: "TECLA",
  };

  $("approvalActionBadge").textContent = actionNames[approval.action] || String(approval.action || "ACÇÃO").toUpperCase();
  $("approvalLabel").textContent = approval.label || "Acção pendente";
  $("approvalTarget").textContent = approval.target || "—";

  if (approval.secret) {
    const length = approval.value_length == null ? "" : ` — ${approval.value_length} caracteres`;
    $("approvalValue").textContent = `Oculto${length}`;
  } else if (approval.value_preview != null && String(approval.value_preview).length) {
    $("approvalValue").textContent = approval.value_preview;
  } else {
    $("approvalValue").textContent = "Sem valor a mostrar";
  }

  $("approvalPage").textContent = approval.title
    ? `${approval.title} — ${approval.url || ""}`
    : (approval.url || "—");

  $("approvalDescription").textContent =
    "O cursor já está no alvo no Chromium. Confirma se queres executar esta acção.";

  $("approveApprovalButton").dataset.approvalId = approval.id;
  $("rejectApprovalButton").dataset.approvalId = approval.id;
  $("approveApprovalButton").disabled = state.approvalBusy;
  $("rejectApprovalButton").disabled = state.approvalBusy;
}

function renderActivity(activity, testRunning) {
  const badge = $("activityBadge");
  badge.textContent = testRunning ? "A executar" : "Em espera";
  badge.className = `badge ${testRunning ? "badge-pass" : "badge-neutral"}`;

  if (!activity || !activity.length) {
    $("activityFeed").innerHTML = '<div class="empty-activity">As acções aparecerão aqui enquanto vês o Chromium a trabalhar.</div>';
    return;
  }

  $("activityFeed").innerHTML = activity.slice().reverse().map((item) => {
    const time = new Date(item.timestamp * 1000).toLocaleTimeString("pt-PT");
    return `
      <div class="activity-item">
        <span class="activity-type">${escapeHtml(item.action)}</span>
        <span class="activity-message">${escapeHtml(item.message)}</span>
        <time>${escapeHtml(time)}</time>
      </div>
    `;
  }).join("");
}

function renderLastTest(test) {
  if (!test) {
    $("emptyExecution").classList.remove("hidden");
    $("testResult").classList.add("hidden");
    return;
  }

  if (test.status === "RUNNING") {
    $("emptyExecution").classList.remove("hidden");
    $("emptyExecution").textContent = test.message || "Teste visual em execução…";
    $("testResult").classList.add("hidden");
    $("testBadge").textContent = "RUNNING";
    $("testBadge").className = "badge badge-warn";
    return;
  }

  $("emptyExecution").classList.add("hidden");
  $("testResult").classList.remove("hidden");
  $("resultTitle").textContent = test.title || test.url || "—";
  $("resultElements").textContent = test.interactive_elements ?? "—";
  $("resultInspected").textContent = test.visually_inspected ?? "—";
  $("resultReport").textContent = test.report_dir || "—";

  const badge = $("testBadge");
  badge.textContent = test.status || "—";
  badge.className = `badge ${test.ok ? "badge-pass" : "badge-fail"}`;
}

async function refresh() {
  try {
    const status = await request("/api/status");
    renderProviders(status.providers);
    renderSession(status.session, status.test_running);
    renderApproval(status.pending_approval);
    renderActivity(status.activity, status.test_running);
    renderLastTest(status.last_test);

    $("safetyBadge").textContent = status.safe_mode ? "Modo seguro" : "Acções perigosas permitidas";
    $("safetyBadge").className = `badge ${status.safe_mode ? "badge-safe" : "badge-fail"}`;
    $("refreshState").textContent = status.pending_approval
      ? "A aguardar a tua aprovação"
      : status.test_running
        ? "Agente em execução"
        : "Actualizado agora";
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
  state.loginModalSuppressed = false;
  await request("/api/session/open", { method: "POST" });
  toast("Chromium aberto. Observa o agente navegar até ao MySANA.");
}));

$("closeSessionButton").addEventListener("click", () => withBusy($("closeSessionButton"), async () => {
  await request("/api/session/close", { method: "POST" });
  setLoginModal(false);
  toast("Sessão Chromium fechada.");
}));

$("startTestButton").addEventListener("click", () => withBusy($("startTestButton"), async () => {
  const result = await request("/api/tests/start", { method: "POST" });
  toast(result.message);
}));

$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (state.busy) return;

  const button = $("loginSubmitButton");
  const username = $("loginUsername").value;
  const password = $("loginPassword").value;

  if (!password) {
    toast("Introduz a password.");
    return;
  }

  state.busy = true;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "A aguardar aprovações…";

  // Remove the credential values from the DOM immediately. The request body
  // keeps them only long enough to execute the local assisted login.
  $("loginPassword").value = "";
  setLoginModal(false);

  try {
    await request("/api/session/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    toast("Login assistido concluído.");
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
    button.disabled = false;
    button.textContent = previous;
    await refresh();
  }
});

async function resolveApproval(approved) {
  if (state.approvalBusy) return;

  const button = approved ? $("approveApprovalButton") : $("rejectApprovalButton");
  const approvalId = button.dataset.approvalId;
  if (!approvalId) return;

  state.approvalBusy = true;
  $("approveApprovalButton").disabled = true;
  $("rejectApprovalButton").disabled = true;

  try {
    await request(
      `/api/approvals/${encodeURIComponent(approvalId)}/${approved ? "approve" : "reject"}`,
      { method: "POST" }
    );
    toast(approved ? "Acção aprovada." : "Acção rejeitada.");
  } catch (error) {
    toast(error.message);
  } finally {
    state.approvalBusy = false;
    await refresh();
  }
}

$("approveApprovalButton").addEventListener("click", () => resolveApproval(true));
$("rejectApprovalButton").addEventListener("click", () => resolveApproval(false));

$("loginCancelButton").addEventListener("click", () => {
  state.loginModalSuppressed = true;
  $("loginPassword").value = "";
  setLoginModal(false);
  toast("Podes escrever manualmente directamente no Chromium.");
});

refresh();
window.setInterval(refresh, 1000);
