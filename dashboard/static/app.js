const state = {
  busy: false,
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
          <span>${
            provider.paid
              ? (provider.role === "primary" ? "primário pago" : "fallback pago")
              : (provider.role === "primary" ? "primário gratuito" : "fallback gratuito")
          }${provider.vision ? " · visão" : ""}</span>
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
  badge.textContent = open
    ? (session.login_required ? "Login necessário" : "Aberta")
    : error
      ? "Erro"
      : "Fechada";

  $("sessionTitle").textContent = session.title || session.message || "Chromium QA ainda não foi aberto";
  $("sessionUrl").textContent = session.url || "mysana.sanahotels.com";
  $("browserName").textContent = session.browser || "Chromium";

  $("closeSessionButton").disabled = !open || state.busy || testRunning;
  $("openSessionButton").disabled = state.busy || testRunning;
  $("agentCommand").disabled = !open || state.busy || testRunning;
  $("planAgentButton").disabled = !open || state.busy || testRunning;

  if (session.login_required && !state.loginModalSuppressed && !state.busy) {
    setLoginModal(true);
  } else if (!session.login_required) {
    setLoginModal(false);
    state.loginModalSuppressed = false;
    $("loginPassword").value = "";
  }
}

function renderPlan(plan, goal, testRunning) {
  const panel = $("planPanel");
  const messages = $("chatMessages");

  if (!goal && !plan) {
    panel.classList.add("hidden");
    messages.innerHTML = `
      <div class="chat-message chat-agent">
        <span class="chat-role">Agente</span>
        <p>Abre o Chromium, navega até ao ecrã pretendido e escreve aqui a tarefa. Primeiro vou gerar o plano; nada será executado nessa fase.</p>
      </div>
    `;
    return;
  }

  const userBubble = goal
    ? `
      <div class="chat-message chat-user">
        <span class="chat-role">Tu</span>
        <p>${escapeHtml(goal)}</p>
      </div>
    `
    : "";

  const agentBubble = plan
    ? `
      <div class="chat-message chat-agent">
        <span class="chat-role">Agente</span>
        <p>${escapeHtml(plan.summary || "Plano pronto.")}</p>
      </div>
    `
    : "";

  messages.innerHTML = userBubble + agentBubble;

  if (!plan) {
    panel.classList.add("hidden");
    return;
  }

  panel.classList.remove("hidden");
  $("planSummary").textContent = plan.summary || "Plano proposto";
  $("planModelBadge").textContent = plan.model || "modelo";
  $("planSteps").innerHTML = (plan.steps || [])
    .map((step) => `<li>${escapeHtml(step)}</li>`)
    .join("");

  $("executePlanButton").disabled = state.busy || testRunning;
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

  const actionLabel =
    actionNames[approval.action] || String(approval.action || "ACÇÃO").toUpperCase();

  $("approvalActionBadge").textContent = actionLabel;
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
  $("approveAlwaysButton").dataset.approvalId = approval.id;
  $("rejectApprovalButton").dataset.approvalId = approval.id;

  const canRemember = ["click", "write", "select"].includes(approval.action);
  $("approveAlwaysButton").classList.toggle("hidden", !canRemember);
  $("approveAlwaysButton").textContent = canRemember
    ? `Sempre aprovar ${actionLabel}`
    : "Sempre aprovar este tipo";

  $("approveApprovalButton").disabled = state.approvalBusy;
  $("approveAlwaysButton").disabled = state.approvalBusy || !canRemember;
  $("rejectApprovalButton").disabled = state.approvalBusy;

  $("approvalHint").textContent = approval.secret
    ? "Esta acção contém um valor secreto. Mesmo que actives a regra ESCREVER, passwords e outros valores secretos continuarão a pedir aprovação manual."
    : "Podes aprovar apenas esta acção ou activar uma regra para este tipo até fechares a sessão Chromium.";
}

function renderApprovalRules(rules) {
  const panel = $("approvalRulesPanel");
  const list = $("approvalRulesList");

  if (!rules || !rules.length) {
    panel.classList.add("hidden");
    list.innerHTML = "";
    return;
  }

  panel.classList.remove("hidden");
  list.innerHTML = rules.map((rule) => `
    <div class="approval-rule-chip">
      <div>
        <strong>Sempre aprovar: ${escapeHtml(rule.label)}</strong>
        <span>${rule.secret_exception ? "Excepto passwords/valores secretos" : "Válida até fechar a sessão"}</span>
      </div>
      <button
        class="button button-ghost approval-rule-disable"
        type="button"
        data-disable-rule="${escapeHtml(rule.action)}"
      >
        Desactivar
      </button>
    </div>
  `).join("");
}

function renderAgentMonitor(agentState, testRunning, steeringQueueSize) {
  const agent = agentState || {};
  const phaseNames = {
    idle: "EM ESPERA",
    planning: "A PLANEAR",
    "plan-ready": "PLANO PRONTO",
    observing: "A OBSERVAR",
    thinking: "A ANALISAR",
    decision: "DECISÃO PRONTA",
    acting: "A EXECUTAR",
    verifying: "A VERIFICAR",
    recovering: "A RECUPERAR",
    done: "CONCLUÍDO",
    stopped: "PARADO",
    blocked: "BLOQUEADO",
  };

  const phase = agent.phase || "idle";
  const phaseBadge = $("agentPhaseBadge");
  phaseBadge.textContent = phaseNames[phase] || String(phase).toUpperCase();
  phaseBadge.className = `badge ${
    ["done", "plan-ready"].includes(phase)
      ? "badge-pass"
      : ["blocked", "stopped"].includes(phase)
        ? "badge-fail"
        : ["thinking", "planning", "recovering", "decision"].includes(phase)
          ? "badge-warn"
          : "badge-neutral"
  }`;

  $("agentStep").textContent = agent.step == null ? "—" : agent.step;
  $("agentProvider").textContent = agent.provider || "—";
  $("agentSteeringCount").textContent = steeringQueueSize || 0;
  $("agentDetail").textContent = agent.detail || "Agente em espera.";

  const startedAt = Number(agent.phase_started_at || 0);
  const elapsed = startedAt > 0
    ? Math.max(0, Math.floor(Date.now() / 1000 - startedAt))
    : 0;
  $("agentElapsed").textContent = `${elapsed}s`;

  const trace = agent.last_decision;
  const tracePanel = $("decisionTrace");
  if (trace && (trace.observation || trace.decision || trace.reason)) {
    tracePanel.classList.remove("hidden");
    $("decisionObservation").textContent = trace.observation || "—";
    $("decisionAction").textContent = trace.decision || "—";
    $("decisionReason").textContent = trace.reason || "—";
    $("decisionConfidence").textContent = trace.confidence
      ? String(trace.confidence).toUpperCase()
      : "—";
  } else {
    tracePanel.classList.add("hidden");
  }

  $("agentSteerMessage").disabled = !testRunning;
  $("steerAgentButton").disabled = !testRunning;
  $("stopAgentButton").disabled = !testRunning;
  $("rejectPendingOnSteer").disabled = !testRunning;
}

function renderActivity(activity, testRunning) {
  const badge = $("activityBadge");
  badge.textContent = testRunning ? "A executar" : "Em espera";
  badge.className = `badge ${testRunning ? "badge-pass" : "badge-neutral"}`;

  if (!activity || !activity.length) {
    $("activityFeed").innerHTML =
      '<div class="empty-activity">Os planos, decisões e resultados aparecerão aqui.</div>';
    return;
  }

  $("activityFeed").innerHTML = activity.slice().reverse().map((item) => {
    const time = new Date(item.timestamp * 1000).toLocaleTimeString("pt-PT");
    const className = ["think", "plan", "observe", "recover"].includes(item.action)
      ? " activity-item-reasoning"
      : "";

    return `
      <div class="activity-item${className}">
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
    $("emptyExecution").textContent = test.message || "Agente em execução…";
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
  badge.className = `badge ${test.ok ? "badge-pass" : test.status === "BLOCKED" ? "badge-warn" : "badge-fail"}`;
}

async function refresh() {
  try {
    const status = await request("/api/status");
    $("buildBadge").textContent = status.version ? `build ${status.version}` : "build desconhecida";

    const computerUse = status.computer_use || {};
    $("computerEngineBadge").textContent = computerUse.enabled
      ? `Computer Use · ${computerUse.model || "OpenAI"}`
      : "execução fallback";
    $("computerEngineBadge").className = `badge ${computerUse.enabled ? "badge-pass" : "badge-warn"}`;

    renderProviders(status.providers);
    renderSession(status.session, status.test_running);
    renderPlan(status.current_plan, status.current_goal, status.test_running);
    renderApproval(status.pending_approval);
    renderApprovalRules(status.auto_approve_rules || []);
    renderAgentMonitor(
      status.agent_state,
      status.test_running,
      status.steering_queue_size
    );
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
  button.textContent = "A processar…";
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

$("agentCommandForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const command = $("agentCommand").value.trim();
  if (!command) {
    toast("Escreve primeiro o que queres que o agente faça.");
    return;
  }

  withBusy($("planAgentButton"), async () => {
    const result = await request("/api/agent/plan", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
    toast(result.message);
  });
});

$("executePlanButton").addEventListener("click", () => withBusy($("executePlanButton"), async () => {
  const result = await request("/api/agent/run", { method: "POST" });
  toast(result.message);
}));

$("agentSteerForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const message = $("agentSteerMessage").value.trim();
  if (!message) {
    toast("Escreve a correcção que queres dar ao agente.");
    return;
  }

  const button = $("steerAgentButton");
  button.disabled = true;

  try {
    const result = await request("/api/agent/steer", {
      method: "POST",
      body: JSON.stringify({
        message,
        reject_pending: $("rejectPendingOnSteer").checked,
      }),
    });

    $("agentSteerMessage").value = "";
    toast(
      result.pending_action_rejected
        ? "Correcção enviada e acção pendente rejeitada. O agente vai reavaliar."
        : "Correcção enviada. Será aplicada no próximo ciclo."
    );
  } catch (error) {
    toast(error.message);
  } finally {
    await refresh();
  }
});

$("stopAgentButton").addEventListener("click", async () => {
  const button = $("stopAgentButton");
  button.disabled = true;

  try {
    const result = await request("/api/agent/stop", { method: "POST" });
    toast(result.message);
  } catch (error) {
    toast(error.message);
  } finally {
    await refresh();
  }
});

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

  $("loginPassword").value = "";
  setLoginModal(false);

  try {
    const result = await request("/api/session/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });

    if (result.agent_recovery_started) {
      toast("Credenciais preenchidas. O agente visual está a decidir como concluir o login.");
    } else {
      toast("Login assistido submetido.");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    state.busy = false;
    button.disabled = false;
    button.textContent = previous;
    await refresh();
  }
});

async function resolveApproval(mode) {
  if (state.approvalBusy) return;

  const button = mode === "reject"
    ? $("rejectApprovalButton")
    : mode === "always"
      ? $("approveAlwaysButton")
      : $("approveApprovalButton");

  const approvalId = button.dataset.approvalId;
  if (!approvalId) return;

  state.approvalBusy = true;
  $("approveApprovalButton").disabled = true;
  $("approveAlwaysButton").disabled = true;
  $("rejectApprovalButton").disabled = true;

  const endpoint = mode === "reject"
    ? "reject"
    : mode === "always"
      ? "approve-always"
      : "approve";

  try {
    const result = await request(
      `/api/approvals/${encodeURIComponent(approvalId)}/${endpoint}`,
      { method: "POST" }
    );

    if (mode === "always" && result.remembered_type) {
      toast(`Regra activa: sempre aprovar ${result.remembered_type.toUpperCase()} nesta sessão.`);
    } else {
      toast(mode === "reject" ? "Acção rejeitada." : "Acção aprovada uma vez.");
    }
  } catch (error) {
    toast(error.message);
  } finally {
    state.approvalBusy = false;
    await refresh();
  }
}

$("approveApprovalButton").addEventListener("click", () => resolveApproval("once"));
$("approveAlwaysButton").addEventListener("click", () => resolveApproval("always"));
$("rejectApprovalButton").addEventListener("click", () => resolveApproval("reject"));

$("approvalRulesList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-disable-rule]");
  if (!button) return;

  const action = button.dataset.disableRule;
  button.disabled = true;

  try {
    await request(
      `/api/approval-rules/${encodeURIComponent(action)}`,
      { method: "DELETE" }
    );
    toast(`Regra ${action.toUpperCase()} desactivada.`);
  } catch (error) {
    toast(error.message);
  } finally {
    await refresh();
  }
});

$("loginCancelButton").addEventListener("click", () => {
  state.loginModalSuppressed = true;
  $("loginPassword").value = "";
  setLoginModal(false);
  toast("Podes escrever manualmente directamente no Chromium.");
});

refresh();
window.setInterval(refresh, 1000);
