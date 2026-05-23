"use strict";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderJson(target, payload) {
  target.innerHTML = "<pre class=\"json-block\">" + escapeHtml(JSON.stringify(payload, null, 2)) + "</pre>";
}

function renderMessage(target, message, cssClass) {
  target.innerHTML = "<div class=\"" + cssClass + "\">" + escapeHtml(message) + "</div>";
}

function artifactUrl(jobId, artifactName) {
  return "/api/jobs/" + encodeURIComponent(jobId) + "/artifacts/" + encodeURIComponent(artifactName);
}

function renderArtifactLinks(jobId, artifacts) {
  if (!artifacts || artifacts.length === 0) {
    return "<p class=\"muted\">No artifacts available yet.</p>";
  }
  const items = artifacts.map(
    (artifact) =>
      "<li><a href=\"" +
      artifactUrl(jobId, artifact) +
      "\">" +
      escapeHtml(artifact) +
      "</a></li>",
  );
  return "<ul class=\"job-list\">" + items.join("") + "</ul>";
}

function renderGenerateResult(target, payload) {
  const jobLink =
    "<p><a href=\"/jobs/" + encodeURIComponent(payload.id) + "\">Open job detail page</a></p>";
  const summary =
    "<p><strong>Status:</strong> " +
    escapeHtml(payload.status) +
    "</p><h3>Artifacts</h3>" +
    renderArtifactLinks(payload.id, payload.artifacts || []);
  const rawJson =
    "<details><summary>Raw response JSON</summary><pre class=\"json-block\">" +
    escapeHtml(JSON.stringify(payload, null, 2)) +
    "</pre></details>";
  target.innerHTML = jobLink + summary + rawJson;
}

function getParsedNetlist() {
  const textarea = document.getElementById("netlist-json");
  if (!textarea) {
    throw new Error("Circuit IR textarea not found.");
  }
  return JSON.parse(textarea.value);
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload?.error?.message || ("Request failed with status " + response.status);
    const error = new Error(message);
    error.payload = payload;
    throw error;
  }
  return payload;
}

async function loadDoctor() {
  const target = document.getElementById("doctor-status");
  if (!target) {
    return;
  }
  try {
    const payload = await fetchJson("/api/doctor");
    const items = (payload.checks || []).map(
      (check) =>
        "<li><strong>" +
        escapeHtml(check.name) +
        ":</strong> " +
        escapeHtml(check.detail) +
        " (" +
        (check.ok ? "ok" : "missing") +
        ")</li>",
    );
    target.innerHTML = "<ul class=\"job-list\">" + items.join("") + "</ul>";
  } catch (error) {
    renderMessage(target, error.message, "error-box");
  }
}

function bindFileUpload() {
  const input = document.getElementById("netlist-file");
  const textarea = document.getElementById("netlist-json");
  if (!input || !textarea) {
    return;
  }
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) {
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      textarea.value = String(reader.result || "");
    };
    reader.readAsText(file);
  });
}

function bindValidate() {
  const button = document.getElementById("validate-button");
  const results = document.getElementById("results-panel");
  const symbolsDirInput = document.getElementById("symbols-dir");
  if (!button || !results || !symbolsDirInput) {
    return;
  }
  button.addEventListener("click", async () => {
    try {
      const payload = await fetchJson("/api/netlists/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          netlist_json: getParsedNetlist(),
          symbols_dir: symbolsDirInput.value || null,
        }),
      });
      renderJson(results, payload);
    } catch (error) {
      renderJson(results, error.payload || { error: { message: error.message } });
    }
  });
}

function bindGenerate() {
  const button = document.getElementById("generate-button");
  const results = document.getElementById("results-panel");
  const projectNameInput = document.getElementById("project-name");
  const symbolsDirInput = document.getElementById("symbols-dir");
  if (!button || !results || !projectNameInput || !symbolsDirInput) {
    return;
  }
  button.addEventListener("click", async () => {
    try {
      const payload = await fetchJson("/api/jobs/from-netlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_name: projectNameInput.value,
          netlist_json: getParsedNetlist(),
          symbols_dir: symbolsDirInput.value || null,
          validation: "internal",
        }),
      });
      renderGenerateResult(results, payload);
    } catch (error) {
      renderJson(results, error.payload || { error: { message: error.message } });
    }
  });
}

function bindSymbolSearch() {
  const button = document.getElementById("symbol-search-button");
  const queryInput = document.getElementById("symbol-query");
  const target = document.getElementById("symbol-results");
  if (!button || !queryInput || !target) {
    return;
  }
  button.addEventListener("click", async () => {
    const query = queryInput.value.trim();
    if (!query) {
      renderMessage(target, "Enter a symbol query first.", "error-box");
      return;
    }
    try {
      const payload = await fetchJson("/api/symbols/search?q=" + encodeURIComponent(query));
      if (!payload.results || payload.results.length === 0) {
        renderMessage(target, "No symbols matched that query.", "muted");
        return;
      }
      const rows = payload.results.map(
        (result) =>
          "<tr><td>" +
          escapeHtml(result.library) +
          "</td><td>" +
          escapeHtml(result.name) +
          "</td><td>" +
          escapeHtml(result.qualified_name) +
          "</td></tr>",
      );
      target.innerHTML =
        "<table class=\"results-table\"><thead><tr><th>Library</th><th>Name</th><th>Qualified</th></tr></thead><tbody>" +
        rows.join("") +
        "</tbody></table>";
    } catch (error) {
      renderJson(target, error.payload || { error: { message: error.message } });
    }
  });
}

function bindWizardFormFeedback() {
  const forms = document.querySelectorAll(".wizard-page-form");
  if (!forms.length) {
    return;
  }

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (typeof form.reportValidity === "function" && !form.reportValidity()) {
        return;
      }

      const submitter =
        event.submitter instanceof HTMLButtonElement
          ? event.submitter
          : form.querySelector('button[type="submit"]');
      const feedback = form.querySelector(".wizard-submit-feedback");
      const feedbackText = form.querySelector(".wizard-submit-feedback-text");
      const submitMessage =
        submitter instanceof HTMLButtonElement && submitter.dataset.submitMessage
          ? submitter.dataset.submitMessage
          : "Working...";

      if (submitter instanceof HTMLButtonElement) {
        submitter.disabled = true;
        submitter.classList.add("is-loading");
      }
      if (feedback instanceof HTMLElement) {
        feedback.hidden = false;
      }
      if (feedbackText instanceof HTMLElement) {
        feedbackText.textContent = submitMessage;
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  bindFileUpload();
  bindValidate();
  bindGenerate();
  bindSymbolSearch();
  bindWizardFormFeedback();
  loadDoctor();
});
