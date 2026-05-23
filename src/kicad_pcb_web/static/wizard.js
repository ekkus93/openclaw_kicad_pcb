"use strict";

let wizardState = null;

function wizardSessionUrl(sessionId) {
  return "/api/wizard/sessions/" + encodeURIComponent(sessionId);
}

function escapeWizardHtml(value) {
  return escapeHtml(value);
}

function renderTranscript(session) {
  const target = document.getElementById("wizard-transcript");
  if (!target) {
    return;
  }
  if (!session || !session.messages || session.messages.length === 0) {
    target.innerHTML = "<p class=\"muted\">No wizard session yet.</p>";
    return;
  }
  const rows = session.messages.map(
    (message) =>
      "<div class=\"transcript-entry transcript-" +
      escapeWizardHtml(message.role) +
      "\"><strong>" +
      escapeWizardHtml(message.role) +
      ":</strong> " +
      escapeWizardHtml(message.content) +
      "</div>",
  );
  target.innerHTML = rows.join("");
}

function renderSpecSummary(session) {
  const target = document.getElementById("wizard-spec-summary");
  if (!target) {
    return;
  }
  if (!session || !session.spec) {
    target.innerHTML = "<p class=\"muted\">No circuit specification yet.</p>";
    return;
  }
  const spec = session.spec;
  const blocks = (spec.blocks || [])
    .map(
      (block) =>
        "<li><strong>" +
        escapeWizardHtml(block.name) +
        "</strong> · " +
        escapeWizardHtml(block.block_type) +
        " · " +
        escapeWizardHtml(block.summary) +
        "</li>",
    )
    .join("");
  const rails = (spec.supply_rails || [])
    .map((rail) => "<li>" + escapeWizardHtml(rail.name) + "</li>")
    .join("");
  target.innerHTML =
    "<dl class=\"summary-list\">" +
    "<dt>Purpose</dt><dd>" +
    escapeWizardHtml(spec.purpose || "—") +
    "</dd>" +
    "<dt>Project</dt><dd>" +
    escapeWizardHtml(spec.project_name || session.project_name || "—") +
    "</dd>" +
    "<dt>Inputs</dt><dd>" +
    escapeWizardHtml(String((spec.inputs || []).length)) +
    "</dd>" +
    "<dt>Outputs</dt><dd>" +
    escapeWizardHtml(String((spec.outputs || []).length)) +
    "</dd>" +
    "</dl>" +
    (rails ? "<h3>Supply Rails</h3><ul class=\"job-list\">" + rails + "</ul>" : "") +
    (blocks ? "<h3>Blocks</h3><ul class=\"job-list\">" + blocks + "</ul>" : "");
}

function renderIrSummary(session) {
  const target = document.getElementById("wizard-ir-summary");
  if (!target) {
    return;
  }
  if (!session || !session.ir_validation) {
    target.innerHTML = "<p class=\"muted\">No Circuit IR generated yet.</p>";
    return;
  }
  const validation = session.ir_validation;
  const fixes = (validation.fixes_applied || [])
    .map((fix) => "<li>" + escapeWizardHtml(fix) + "</li>")
    .join("");
  const warnings = (validation.warnings || [])
    .map((warning) => "<li>" + escapeWizardHtml(warning.message || JSON.stringify(warning)) + "</li>")
    .join("");
  let html =
    "<dl class=\"summary-list\">" +
    "<dt>Valid</dt><dd>" +
    escapeWizardHtml(String(validation.valid)) +
    "</dd>" +
    "<dt>Components</dt><dd>" +
    escapeWizardHtml(String(validation.component_count || 0)) +
    "</dd>" +
    "<dt>Nets</dt><dd>" +
    escapeWizardHtml(String(validation.net_count || 0)) +
    "</dd>" +
    "<dt>Auto-fixed</dt><dd>" +
    escapeWizardHtml(String(Boolean(validation.auto_fixed))) +
    "</dd>" +
    "</dl>";
  if (fixes) {
    html += "<h3>Deterministic Fixes</h3><ul class=\"job-list\">" + fixes + "</ul>";
  }
  if (warnings) {
    html += "<h3>Warnings</h3><ul class=\"job-list\">" + warnings + "</ul>";
  }
  if (session.ir_json) {
    html +=
      "<details><summary>Raw Circuit IR JSON</summary><pre class=\"json-block\">" +
      escapeWizardHtml(JSON.stringify(session.ir_json, null, 2)) +
      "</pre></details>";
  }
  target.innerHTML = html;
}

function renderOpenQuestions(session) {
  const target = document.getElementById("wizard-open-questions");
  if (!target) {
    return;
  }
  const items = (session && session.open_questions) || [];
  if (items.length === 0) {
    target.innerHTML = "<p class=\"muted\">No open questions.</p>";
    return;
  }
  target.innerHTML =
    "<ul class=\"job-list\">" +
    items.map((item) => "<li>" + escapeWizardHtml(item) + "</li>").join("") +
    "</ul>";
}

function renderGenerationResult(payload) {
  const target = document.getElementById("wizard-generation-result");
  if (!target) {
    return;
  }
  if (!payload) {
    target.innerHTML = "<p class=\"muted\">No project generation run yet.</p>";
    return;
  }
  target.innerHTML =
    "<p><strong>Job:</strong> <a href=\"/jobs/" +
    encodeURIComponent(payload.job.id) +
    "\">" +
    escapeWizardHtml(payload.job.id) +
    "</a></p>" +
    "<p><strong>Status:</strong> " +
    escapeWizardHtml(payload.job.status) +
    "</p>" +
    "<details><summary>Raw job payload</summary><pre class=\"json-block\">" +
    escapeWizardHtml(JSON.stringify(payload.job, null, 2)) +
    "</pre></details>";
}

function updateWizardButtons(session) {
  const approveButton = document.getElementById("wizard-approve-button");
  const irButton = document.getElementById("wizard-generate-ir-button");
  const projectButton = document.getElementById("wizard-generate-project-button");
  const sessionLabel = document.getElementById("wizard-session-id");
  const statusPill = document.getElementById("wizard-status-pill");
  if (!approveButton || !irButton || !projectButton || !sessionLabel || !statusPill) {
    return;
  }
  if (!session) {
    sessionLabel.textContent = "Not started";
    statusPill.textContent = "idle";
    approveButton.disabled = true;
    irButton.disabled = true;
    projectButton.disabled = true;
    return;
  }
  sessionLabel.textContent = session.id;
  statusPill.textContent = session.status;
  statusPill.className = "status-pill status-active";
  approveButton.disabled = !session.spec || session.spec_approved;
  irButton.disabled = !session.spec_approved;
  projectButton.disabled = !(session.ir_validation && session.ir_validation.valid);
}

function renderWizardSession(session) {
  wizardState = session;
  renderTranscript(session);
  renderSpecSummary(session);
  renderIrSummary(session);
  renderOpenQuestions(session);
  updateWizardButtons(session);
}

async function startOrSendWizardMessage() {
  const messageInput = document.getElementById("wizard-message-input");
  const projectNameInput = document.getElementById("wizard-project-name");
  const symbolsDirInput = document.getElementById("wizard-symbols-dir");
  const target = document.getElementById("wizard-generation-result");
  if (!messageInput || !projectNameInput || !symbolsDirInput || !target) {
    return;
  }
  const message = messageInput.value.trim();
  if (!message) {
    renderMessage(target, "Enter a circuit message first.", "error-box");
    return;
  }
  try {
    let payload;
    if (!wizardState) {
      payload = await fetchJson("/api/wizard/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          project_name: projectNameInput.value || null,
          symbols_dir: symbolsDirInput.value || null,
        }),
      });
    } else {
      payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/messages", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
    }
    messageInput.value = "";
    renderGenerationResult(null);
    renderWizardSession(payload);
  } catch (error) {
    renderJson(target, error.payload || { error: { message: error.message } });
  }
}

async function approveWizardSpec() {
  if (!wizardState) {
    return;
  }
  const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/approve-spec", {
    method: "POST",
  });
  renderWizardSession(payload);
}

async function generateWizardIr() {
  if (!wizardState) {
    return;
  }
  const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/generate-ir", {
    method: "POST",
  });
  renderWizardSession(payload);
}

async function generateWizardProject() {
  if (!wizardState) {
    return;
  }
  const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/generate-project", {
    method: "POST",
  });
  renderWizardSession(payload.session);
  renderGenerationResult(payload);
}

document.addEventListener("DOMContentLoaded", () => {
  const startButton = document.getElementById("wizard-start-button");
  const approveButton = document.getElementById("wizard-approve-button");
  const irButton = document.getElementById("wizard-generate-ir-button");
  const projectButton = document.getElementById("wizard-generate-project-button");
  if (!startButton || !approveButton || !irButton || !projectButton) {
    return;
  }
  startButton.addEventListener("click", () => {
    startOrSendWizardMessage().catch((error) => {
      const target = document.getElementById("wizard-generation-result");
      if (target) {
        renderJson(target, error.payload || { error: { message: error.message } });
      }
    });
  });
  approveButton.addEventListener("click", () => {
    approveWizardSpec().catch((error) => {
      const target = document.getElementById("wizard-generation-result");
      if (target) {
        renderJson(target, error.payload || { error: { message: error.message } });
      }
    });
  });
  irButton.addEventListener("click", () => {
    generateWizardIr().catch((error) => {
      const target = document.getElementById("wizard-generation-result");
      if (target) {
        renderJson(target, error.payload || { error: { message: error.message } });
      }
    });
  });
  projectButton.addEventListener("click", () => {
    generateWizardProject().catch((error) => {
      const target = document.getElementById("wizard-generation-result");
      if (target) {
        renderJson(target, error.payload || { error: { message: error.message } });
      }
    });
  });
});