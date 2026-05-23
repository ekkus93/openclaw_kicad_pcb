"use strict";

let wizardState = null;
let wizardGenerationPayload = null;
let wizardBusy = false;

const STEP_COPY = {
  describe: {
    kicker: "Step 1",
    title: "Describe the circuit",
    detail:
      "Shape the request until the spec is specific enough to review and approve.",
  },
  spec: {
    kicker: "Step 2",
    title: "Review the drafted spec",
    detail: "Approve the human-readable spec or send one short revision note.",
  },
  ir: {
    kicker: "Step 3",
    title: "Inspect validated Circuit IR",
    detail: "Focus on validity, fixes, and warnings before moving to generation.",
  },
  generate: {
    kicker: "Step 4",
    title: "Generate the project",
    detail: "Use the validated IR to launch the normal deterministic generation path.",
  },
};

function wizardSessionUrl(sessionId) {
  return "/api/wizard/sessions/" + encodeURIComponent(sessionId);
}

function escapeWizardHtml(value) {
  return escapeHtml(value);
}

function countList(items) {
  return Array.isArray(items) ? items.length : 0;
}

function renderListOrMuted(items, emptyMessage) {
  if (!items || items.length === 0) {
    return "<p class=\"muted\">" + escapeWizardHtml(emptyMessage) + "</p>";
  }
  return (
    "<ul class=\"job-list\">" +
    items.map((item) => "<li>" + escapeWizardHtml(item) + "</li>").join("") +
    "</ul>"
  );
}

function renderListSection(title, items) {
  if (!items || items.length === 0) {
    return "";
  }
  return "<h4>" + escapeWizardHtml(title) + "</h4>" + renderListOrMuted(items, "None.");
}

function getWizardMessageInputValue() {
  const input = document.getElementById("wizard-message-input");
  return input ? input.value.trim() : "";
}

function deriveWizardUiState(session) {
  const messageDraft = getWizardMessageInputValue();
  const hasDraft = messageDraft.length > 0;
  const model = {
    currentStep: "describe",
    stageSummary: "Ready to start.",
    bannerText: "The wizard is ready to start a new session.",
    bannerClass: "status-neutral",
    primaryLabel: "Start Wizard",
    primaryAction: "start",
    primaryDisabled: wizardBusy,
    showComposer: true,
    composerLabel: "Circuit Request",
    composerPlaceholder:
      "Describe the circuit you want. Include purpose, inputs, outputs, rails, and any constraints.",
    actionCopy:
      "Start with the circuit goal, I/O, supply rails, and any must-use or must-avoid parts.",
    secondary: null,
    stepStates: {
      describe: "active",
      spec: "upcoming",
      ir: "upcoming",
      generate: "upcoming",
    },
  };

  if (!session) {
    return model;
  }

  const status = session.status;
  const openQuestions = countList(session.open_questions);
  const unsupportedReasons = countList(session.unsupported_reasons);

  model.stageSummary = "Current state: " + status.replaceAll("_", " ") + ".";

  if (status === "drafting_spec" || status === "awaiting_user_clarification") {
    model.currentStep = "describe";
    model.primaryLabel = "Send Reply";
    model.primaryAction = "reply";
    model.bannerText =
      openQuestions > 0
        ? "The wizard needs a bit more information before the spec can be reviewed."
        : "Keep refining the request until the spec is ready to review.";
    model.bannerClass = "status-warning";
    model.composerLabel = "Reply to the wizard";
    model.composerPlaceholder =
      "Answer the open questions or refine the design constraints in one concise note.";
    model.actionCopy =
      openQuestions > 0
        ? "Reply directly to the open questions. The next goal is a reviewable spec."
        : "Keep tightening the request until the wizard can produce a reviewable spec.";
    return model;
  }

  model.stepStates.describe = "complete";

  if (status === "spec_ready_for_review") {
    model.currentStep = "spec";
    model.stepStates.spec = unsupportedReasons > 0 || openQuestions > 0 ? "blocked" : "active";
    model.primaryLabel = "Approve Spec";
    model.primaryAction = "approve";
    model.primaryDisabled = wizardBusy || !session.spec || unsupportedReasons > 0 || openQuestions > 0;
    model.bannerText =
      unsupportedReasons > 0
        ? "This spec cannot be approved until the unsupported constraints are resolved."
        : openQuestions > 0
          ? "The spec is close, but unresolved questions still block approval."
          : "The spec is reviewable. Approve it when the summary matches your intent.";
    model.bannerClass = unsupportedReasons > 0 || openQuestions > 0 ? "status-warning" : "status-active";
    model.composerLabel = "Need changes? Send one revision note";
    model.composerPlaceholder =
      "If the spec is close but not right, send one concise revision note here.";
    model.actionCopy =
      unsupportedReasons > 0 || openQuestions > 0
        ? "Resolve the visible blockers with one clear revision note."
        : "Approve the spec now, or send one concise revision note if something is still off.";
    model.secondary = {
      label: "Send Revision Note",
      action: "reply",
      disabled: wizardBusy || !hasDraft,
      hidden: false,
    };
    return model;
  }

  model.stepStates.spec = "complete";

  if (status === "spec_approved" || status === "drafting_ir" || status === "ir_needs_repair") {
    model.currentStep = "ir";
    model.stepStates.ir = status === "ir_needs_repair" ? "blocked" : "active";
    model.primaryLabel = status === "ir_needs_repair" ? "Retry Circuit IR" : "Generate Circuit IR";
    model.primaryAction = "generate-ir";
    model.primaryDisabled = wizardBusy;
    model.showComposer = false;
    model.actionCopy =
      status === "ir_needs_repair"
        ? "The last IR attempt did not validate. Retry after reviewing the validation feedback below."
        : "The spec is approved. Generate Circuit IR when you are ready to inspect the validation result.";
    model.bannerText =
      status === "ir_needs_repair"
        ? "The IR still needs repair before project generation can begin."
        : "The spec is locked. The next checkpoint is validated Circuit IR.";
    model.bannerClass = status === "ir_needs_repair" ? "status-warning" : "status-active";
    return model;
  }

  model.stepStates.ir = "complete";

  if (status === "ir_ready_for_generation" || status === "generation_started") {
    model.currentStep = "generate";
    model.stepStates.generate = "active";
    model.primaryLabel = status === "generation_started" ? "Generating Project" : "Generate Project";
    model.primaryAction = "generate-project";
    model.primaryDisabled = wizardBusy || status === "generation_started";
    model.showComposer = false;
    model.actionCopy =
      status === "generation_started"
        ? "The job has been launched. Use the generated job link below to inspect progress and artifacts."
        : "The IR is valid. Launch the deterministic generation job when you are ready.";
    model.bannerText =
      status === "generation_started"
        ? "Project generation has started."
        : "The IR is valid and ready for project generation.";
    model.bannerClass = "status-active";
    return model;
  }

  if (status === "completed") {
    model.currentStep = "generate";
    model.stepStates.generate = "complete";
    model.primaryLabel = "Start New Wizard";
    model.primaryAction = "reset";
    model.primaryDisabled = wizardBusy;
    model.showComposer = false;
    model.actionCopy =
      "The generation handoff is complete. Open the job detail page or start a fresh wizard session.";
    model.bannerText = "Project generation finished. Review the result and artifacts below.";
    model.bannerClass = "status-success";
    return model;
  }

  if (status === "failed") {
    if (session.ir_json || session.ir_validation) {
      model.currentStep = "generate";
      model.stepStates.generate = "blocked";
      model.primaryLabel = "Start New Wizard";
      model.primaryAction = "reset";
      model.primaryDisabled = wizardBusy;
      model.showComposer = false;
      model.bannerText = "The last wizard action failed. Inspect the error summary before starting over.";
      model.bannerClass = "status-error";
      model.actionCopy = "Use the error summary below to decide whether to retry externally or start fresh.";
      return model;
    }

    model.currentStep = "spec";
    model.stepStates.spec = "blocked";
    model.primaryLabel = "Send Revision Note";
    model.primaryAction = "reply";
    model.primaryDisabled = wizardBusy || !hasDraft;
    model.bannerText = "The wizard could not produce a usable spec from the last request.";
    model.bannerClass = "status-error";
    model.composerLabel = "Refine the request";
    model.composerPlaceholder = "Clarify the request or remove the unsupported constraints.";
    model.actionCopy = "Refine the request with one concise note, then try again.";
    return model;
  }

  return model;
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
      "\"><div class=\"transcript-entry-meta\">" +
      escapeWizardHtml(message.role === "assistant" ? "Wizard" : "You") +
      "</div><div class=\"transcript-entry-body\">" +
      escapeWizardHtml(message.content) +
      "</div></div>",
  );
  target.innerHTML = rows.join("");
}

function buildSpecSummaryHtml(session) {
  if (!session || !session.spec) {
    return "<p class=\"muted\">No circuit specification yet.</p>";
  }
  const spec = session.spec;
  const blocks = (spec.blocks || [])
    .map(
      (block) =>
        "<li><strong>" +
        escapeWizardHtml(block.name) +
        "</strong><span class=\"wizard-inline-chip\">" +
        escapeWizardHtml(block.block_type) +
        "</span><p class=\"muted\">" +
        escapeWizardHtml(block.summary) +
        "</p></li>",
    )
    .join("");
  const rails = (spec.supply_rails || [])
    .map(
      (rail) =>
        "<li><strong>" +
        escapeWizardHtml(rail.name) +
        "</strong>" +
        (rail.nominal_voltage
          ? " <span class=\"muted\">" + escapeWizardHtml(rail.nominal_voltage) + "</span>"
          : "") +
        "</li>",
    )
    .join("");

  return (
    "<dl class=\"summary-list wizard-readable-summary\">" +
    "<dt>Purpose</dt><dd>" +
    escapeWizardHtml(spec.purpose || "—") +
    "</dd>" +
    "<dt>Project</dt><dd>" +
    escapeWizardHtml(spec.project_name || session.project_name || "—") +
    "</dd></dl>" +
    "<div class=\"wizard-stat-grid\">" +
    "<div class=\"wizard-stat-card\"><span>Inputs</span><strong>" +
    escapeWizardHtml(String(countList(spec.inputs))) +
    "</strong></div>" +
    "<div class=\"wizard-stat-card\"><span>Outputs</span><strong>" +
    escapeWizardHtml(String(countList(spec.outputs))) +
    "</strong></div>" +
    "<div class=\"wizard-stat-card\"><span>Blocks</span><strong>" +
    escapeWizardHtml(String(countList(spec.blocks))) +
    "</strong></div></div>" +
    (rails ? "<h4>Supply Rails</h4><ul class=\"job-list\">" + rails + "</ul>" : "") +
    renderListSection(
      "Inputs",
      (spec.inputs || []).map((item) => item.name || "Unnamed input"),
    ) +
    renderListSection(
      "Outputs",
      (spec.outputs || []).map((item) => item.name || "Unnamed output"),
    ) +
    (blocks ? "<h4>Functional Blocks</h4><ul class=\"job-list wizard-block-list\">" + blocks + "</ul>" : "") +
    renderListSection("Assumptions", spec.assumptions || [])
  );
}

function renderSpecSummary(session) {
  const target = document.getElementById("wizard-spec-summary");
  if (!target) {
    return;
  }
  target.innerHTML = buildSpecSummaryHtml(session);
}

function buildIrSummaryHtml(session) {
  if (!session || !session.ir_validation) {
    return "<p class=\"muted\">No Circuit IR generated yet.</p>";
  }
  const validation = session.ir_validation;
  const fixes = (validation.fixes_applied || [])
    .map((fix) => "<li>" + escapeWizardHtml(fix) + "</li>")
    .join("");
  const warnings = (validation.warnings || [])
    .map((warning) => "<li>" + escapeWizardHtml(warning.message || JSON.stringify(warning)) + "</li>")
    .join("");

  let html =
    "<div class=\"wizard-validation-banner " +
    (validation.valid ? "status-success" : "status-warning") +
    "\">" +
    (validation.valid
      ? "The Circuit IR is valid and ready for the generation handoff."
      : escapeWizardHtml(validation.error_message || "The Circuit IR still needs repair.")) +
    "</div>" +
    "<div class=\"wizard-stat-grid\">" +
    "<div class=\"wizard-stat-card\"><span>Valid</span><strong>" +
    escapeWizardHtml(validation.valid ? "Yes" : "No") +
    "</strong></div>" +
    "<div class=\"wizard-stat-card\"><span>Components</span><strong>" +
    escapeWizardHtml(String(validation.component_count || 0)) +
    "</strong></div>" +
    "<div class=\"wizard-stat-card\"><span>Nets</span><strong>" +
    escapeWizardHtml(String(validation.net_count || 0)) +
    "</strong></div>" +
    "<div class=\"wizard-stat-card\"><span>Auto-fix</span><strong>" +
    escapeWizardHtml(validation.auto_fixed ? "Applied" : "Not needed") +
    "</strong></div></div>";

  if (fixes) {
    html += "<h4>Deterministic Fixes</h4><ul class=\"job-list\">" + fixes + "</ul>";
  }
  if (warnings) {
    html += "<h4>Warnings</h4><ul class=\"job-list\">" + warnings + "</ul>";
  }
  if (session.ir_json) {
    html +=
      "<details><summary>Raw Circuit IR JSON</summary><pre class=\"json-block\">" +
      escapeWizardHtml(JSON.stringify(session.ir_json, null, 2)) +
      "</pre></details>";
  }
  return html;
}

function renderIrSummary(session) {
  const target = document.getElementById("wizard-ir-summary");
  if (!target) {
    return;
  }
  target.innerHTML = buildIrSummaryHtml(session);
}

function renderOpenQuestions(session) {
  const target = document.getElementById("wizard-open-questions");
  if (!target) {
    return;
  }
  const items = (session && session.open_questions) || [];
  target.innerHTML = "<h4>Open Questions</h4>" + renderListOrMuted(items, "No open questions.");
}

function renderUnsupportedSummary(session) {
  const target = document.getElementById("wizard-unsupported-summary");
  if (!target) {
    return;
  }
  const items = (session && session.unsupported_reasons) || [];
  target.innerHTML =
    "<h4>Unsupported Constraints</h4>" +
    renderListOrMuted(items, "No unsupported constraints flagged.");
}

function renderGenerationResult(payload) {
  const target = document.getElementById("wizard-generation-result");
  if (!target) {
    return;
  }
  if (!payload) {
    if (wizardState && wizardState.status === "ir_ready_for_generation") {
      target.innerHTML =
        "<div class=\"wizard-validation-banner status-active\">The IR is ready. The next handoff is project generation.</div>" +
        "<p class=\"muted\">Press Generate Project when you are ready.</p>";
      return;
    }
    target.innerHTML = "<p class=\"muted\">No project generation run yet.</p>";
    return;
  }
  target.innerHTML =
    "<div class=\"wizard-validation-banner status-success\">The generation job was created successfully.</div>" +
    "<p><strong>Job:</strong> <a href=\"/jobs/" +
    encodeURIComponent(payload.job.id) +
    "\">" +
    escapeWizardHtml(payload.job.id) +
    "</a></p>" +
    "<p><strong>Status:</strong> " +
    escapeWizardHtml(payload.job.status) +
    "</p>" +
    "<p class=\"muted\">Open the job detail page to inspect warnings, artifacts, and raw output.</p>" +
    "<details><summary>Raw job payload</summary><pre class=\"json-block\">" +
    escapeWizardHtml(JSON.stringify(payload.job, null, 2)) +
    "</pre></details>";
}

function renderInlineStatus(payload, cssClass) {
  const target = document.getElementById("wizard-inline-status");
  if (!target) {
    return;
  }
  if (typeof payload === "string") {
    renderMessage(target, payload, cssClass);
    return;
  }
  renderJson(target, payload);
}

function clearInlineStatus() {
  renderInlineStatus("No requests submitted yet.", "muted");
}

function updateWizardButtons(session) {
  const primaryButton = document.getElementById("wizard-primary-button");
  const secondaryButton = document.getElementById("wizard-secondary-button");
  const messageInput = document.getElementById("wizard-message-input");
  const messageLabel = document.getElementById("wizard-message-label");
  const actionCopy = document.getElementById("wizard-action-copy");
  const sessionLabel = document.getElementById("wizard-session-id");
  const statusPill = document.getElementById("wizard-status-pill");
  const stageSummary = document.getElementById("wizard-stage-summary");
  const stepKicker = document.getElementById("wizard-current-step-kicker");
  const stepTitle = document.getElementById("wizard-current-step-title");
  const stepDetail = document.getElementById("wizard-current-step-detail");
  const banner = document.getElementById("wizard-stage-banner");
  if (
    !primaryButton ||
    !secondaryButton ||
    !messageInput ||
    !messageLabel ||
    !actionCopy ||
    !sessionLabel ||
    !statusPill ||
    !stageSummary ||
    !stepKicker ||
    !stepTitle ||
    !stepDetail ||
    !banner
  ) {
    return;
  }

  const ui = deriveWizardUiState(session);

  sessionLabel.textContent = session ? session.id : "Not started";
  statusPill.textContent = session ? session.status.replaceAll("_", " ") : "idle";
  statusPill.className = "status-pill " + (session ? ui.bannerClass : "status-neutral");
  stageSummary.textContent = ui.stageSummary;
  stepKicker.textContent = STEP_COPY[ui.currentStep].kicker;
  stepTitle.textContent = STEP_COPY[ui.currentStep].title;
  stepDetail.textContent = STEP_COPY[ui.currentStep].detail;
  banner.textContent = ui.bannerText;
  banner.className = "wizard-stage-banner " + ui.bannerClass;
  actionCopy.textContent = ui.actionCopy;
  messageLabel.textContent = ui.composerLabel;
  messageInput.placeholder = ui.composerPlaceholder;
  messageInput.disabled = wizardBusy || !ui.showComposer;
  messageInput.hidden = !ui.showComposer;
  messageLabel.hidden = !ui.showComposer;

  primaryButton.textContent = ui.primaryLabel;
  primaryButton.dataset.action = ui.primaryAction;
  primaryButton.disabled = ui.primaryDisabled;

  if (ui.secondary && !ui.secondary.hidden) {
    secondaryButton.hidden = false;
    secondaryButton.disabled = ui.secondary.disabled;
    secondaryButton.textContent = ui.secondary.label;
    secondaryButton.dataset.action = ui.secondary.action;
  } else {
    secondaryButton.hidden = true;
    secondaryButton.disabled = true;
    secondaryButton.dataset.action = "";
  }

  document.querySelectorAll(".wizard-step").forEach((step) => {
    const state = ui.stepStates[step.dataset.step] || "upcoming";
    step.className = "wizard-step is-" + state;
    step.setAttribute("aria-current", step.dataset.step === ui.currentStep ? "step" : "false");
  });

  document.querySelectorAll(".wizard-stage-panel").forEach((panel) => {
    panel.hidden = panel.dataset.step !== ui.currentStep;
  });
}

function renderWizardSession(session) {
  wizardState = session;
  renderTranscript(session);
  renderSpecSummary(session);
  renderIrSummary(session);
  renderOpenQuestions(session);
  renderUnsupportedSummary(session);
  updateWizardButtons(session);
  if (!wizardGenerationPayload) {
    renderGenerationResult(null);
  }
}

function setWizardBusy(nextBusy) {
  wizardBusy = nextBusy;
  updateWizardButtons(wizardState);
}

async function startOrSendWizardMessage() {
  const messageInput = document.getElementById("wizard-message-input");
  const projectNameInput = document.getElementById("wizard-project-name");
  const symbolsDirInput = document.getElementById("wizard-symbols-dir");
  if (!messageInput || !projectNameInput || !symbolsDirInput) {
    return;
  }
  const message = messageInput.value.trim();
  if (!message) {
    renderInlineStatus("Enter a circuit message first.", "error-box");
    return;
  }
  try {
    setWizardBusy(true);
    renderInlineStatus(
      wizardState ? "Sending your reply to the wizard..." : "Starting the wizard session...",
      "panel-note",
    );
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
    wizardGenerationPayload = null;
    clearInlineStatus();
    renderWizardSession(payload);
  } catch (error) {
    renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
  } finally {
    setWizardBusy(false);
  }
}

async function approveWizardSpec() {
  if (!wizardState) {
    return;
  }
  try {
    setWizardBusy(true);
    renderInlineStatus("Approving the spec checkpoint...", "panel-note");
    const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/approve-spec", {
      method: "POST",
    });
    clearInlineStatus();
    renderWizardSession(payload);
  } catch (error) {
    renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
  } finally {
    setWizardBusy(false);
  }
}

async function generateWizardIr() {
  if (!wizardState) {
    return;
  }
  try {
    setWizardBusy(true);
    renderInlineStatus("Generating Circuit IR and running validation...", "panel-note");
    const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/generate-ir", {
      method: "POST",
    });
    clearInlineStatus();
    renderWizardSession(payload);
  } catch (error) {
    renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
  } finally {
    setWizardBusy(false);
  }
}

async function generateWizardProject() {
  if (!wizardState) {
    return;
  }
  try {
    setWizardBusy(true);
    renderInlineStatus("Starting project generation from the validated IR...", "panel-note");
    const payload = await fetchJson(wizardSessionUrl(wizardState.id) + "/generate-project", {
      method: "POST",
    });
    wizardGenerationPayload = payload;
    clearInlineStatus();
    renderWizardSession(payload.session);
    renderGenerationResult(payload);
  } catch (error) {
    renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
    const target = document.getElementById("wizard-generation-result");
    if (target) {
      renderJson(target, error.payload || { error: { message: error.message } });
    }
  } finally {
    setWizardBusy(false);
  }
}

function resetWizard() {
  wizardState = null;
  wizardGenerationPayload = null;
  const messageInput = document.getElementById("wizard-message-input");
  if (messageInput) {
    messageInput.value = "";
  }
  clearInlineStatus();
  renderWizardSession(null);
  renderGenerationResult(null);
}

async function performWizardAction(action) {
  if (action === "start" || action === "reply") {
    await startOrSendWizardMessage();
    return;
  }
  if (action === "approve") {
    await approveWizardSpec();
    return;
  }
  if (action === "generate-ir") {
    await generateWizardIr();
    return;
  }
  if (action === "generate-project") {
    await generateWizardProject();
    return;
  }
  if (action === "reset") {
    resetWizard();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const primaryButton = document.getElementById("wizard-primary-button");
  const secondaryButton = document.getElementById("wizard-secondary-button");
  const resetButton = document.getElementById("wizard-reset-button");
  const messageInput = document.getElementById("wizard-message-input");
  if (!primaryButton || !secondaryButton || !resetButton || !messageInput) {
    return;
  }

  primaryButton.addEventListener("click", () => {
    performWizardAction(primaryButton.dataset.action).catch((error) => {
      renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
    });
  });

  secondaryButton.addEventListener("click", () => {
    performWizardAction(secondaryButton.dataset.action).catch((error) => {
      renderInlineStatus(error.payload || { error: { message: error.message } }, "error-box");
    });
  });

  resetButton.addEventListener("click", () => {
    resetWizard();
  });

  messageInput.addEventListener("input", () => {
    updateWizardButtons(wizardState);
  });

  renderWizardSession(null);
  clearInlineStatus();
});