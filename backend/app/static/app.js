const THEME_KEY = "rewind-theme";

const statusChip = document.getElementById("statusChip");
const captureStatus = document.getElementById("captureStatus");
const timePreview = document.getElementById("timePreview");
const answerOutput = document.getElementById("answerOutput");
const cleanedQuery = document.getElementById("cleanedQuery");
const results = document.getElementById("results");
const recentObservations = document.getElementById("recentObservations");
const queryInput = document.getElementById("queryInput");
const timeFilterInput = document.getElementById("timeFilterInput");
const runQueryBtn = document.getElementById("runQuery");
const previewTimeBtn = document.getElementById("previewTime");
const themeToggle = document.getElementById("themeToggle");
const themeToggleLabel = document.getElementById("themeToggleLabel");

marked.use({ breaks: true, gfm: true });

function getSystemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.classList.toggle("dark", theme === "dark");
  themeToggleLabel.textContent = theme === "dark" ? "Light" : "Dark";
  themeToggle.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  themeToggle.setAttribute(
    "aria-label",
    theme === "dark" ? "Switch to light mode" : "Switch to dark mode",
  );
}

function initializeTheme() {
  const savedTheme = localStorage.getItem(THEME_KEY);
  applyTheme(savedTheme || getSystemTheme());
}

async function fetchJSON(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with ${response.status}`);
  }

  return response.json();
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatCompactDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, {
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatScore(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : null;
}

function escapeHtml(text) {
  if (!text) return "";
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderStatus(data) {
  statusChip.innerHTML = `<span class="status-indicator"></span> ${
    data.running ? '<span class="status-chip__label">Live</span>' : '<span class="status-chip__label">Idle</span>'
  }`;
  statusChip.classList.toggle("running", Boolean(data.running));
  statusChip.title = data.running ? "Capture loop running" : "Capture loop stopped";

  captureStatus.innerHTML = "";
  const pairs = [
    {
      label: "Int",
      value: `${data.interval_seconds}s`,
      title: `Capture interval: ${data.interval_seconds} seconds`,
    },
    {
      label: "Seen",
      value: String(data.stats.windows_seen ?? 0),
      title: `Windows seen: ${data.stats.windows_seen ?? 0}`,
    },
    {
      label: "Obs",
      value: String(data.stats.total_observations ?? 0),
      title: `Indexed observations: ${data.stats.total_observations ?? 0}`,
    },
    {
      label: "New",
      value: String(data.stats.observations_inserted ?? 0),
      title: `Inserted observations: ${data.stats.observations_inserted ?? 0}`,
    },
    {
      label: "Last",
      value: data.last_run_completed_at ? formatCompactDateTime(data.last_run_completed_at) : "-",
      title: data.last_run_completed_at
        ? `Last completed: ${formatDateTime(data.last_run_completed_at)}`
        : "No completed run yet",
    },
    {
      label: "Err",
      value: data.last_error ? "Issue" : "OK",
      title: data.last_error || "No recent capture errors",
    },
  ];

  for (const pair of pairs) {
    const wrapper = document.createElement("div");
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = pair.label;
    dd.textContent = pair.value;
    wrapper.title = pair.title;
    wrapper.append(dt, dd);
    captureStatus.append(wrapper);
  }
}

function renderTimePreview(data) {
  if (!data || (!data.start && !data.end)) {
    timePreview.textContent = "No time filter applied.";
    return;
  }

  timePreview.innerHTML = [
    `<strong>Active filter</strong>`,
    `${escapeHtml(formatDateTime(data.start))} to ${escapeHtml(formatDateTime(data.end))}`,
    data.matched_text ? `Matched "${escapeHtml(data.matched_text)}"` : null,
  ]
    .filter(Boolean)
    .join(" | ");
}

function resultCard(item, options = {}) {
  const { showScore = true } = options;
  const score = showScore ? formatScore(item.score) : null;
  const badges = [
    `${escapeHtml(item.capture_count)} captures`,
    score ? `Score ${score}` : null,
  ]
    .filter(Boolean)
    .map((label) => `<span class="badge">${label}</span>`)
    .join("");

  return `
    <article class="observation-card">
      <div class="observation-card__header">
        <div>
          <p class="observation-card__eyebrow">${escapeHtml(formatDateTime(item.last_seen_at))}</p>
          <h3 title="${escapeHtml(item.window_title)}">${escapeHtml(item.window_title)}</h3>
        </div>
      </div>

      <div class="observation-card__meta">
        ${escapeHtml(formatDateTime(item.first_seen_at))} to ${escapeHtml(formatDateTime(item.last_seen_at))}
      </div>

      <div class="badge-row">${badges}</div>

      <img src="${escapeHtml(item.screenshot_url)}" alt="Screenshot of ${escapeHtml(item.window_title)}" loading="lazy" />

      <details>
        <summary>Extracted markdown</summary>
        <pre>${escapeHtml(item.markdown)}</pre>
      </details>
      ${
        item.notes
          ? `
      <details>
        <summary>Notes</summary>
        <pre>${escapeHtml(item.notes)}</pre>
      </details>`
          : ""
      }
    </article>
  `;
}

function setEmptyState(container, className, message) {
  container.className = `${className} empty-state`;
  container.textContent = message;
}

function renderResults(items) {
  if (!items || items.length === 0) {
    setEmptyState(results, "observation-rail", "No retrieval results match your query.");
    return;
  }

  results.className = "observation-rail";
  results.innerHTML = items.map((item) => resultCard(item)).join("");
}

function renderRecent(items) {
  if (!items || items.length === 0) {
    setEmptyState(
      recentObservations,
      "recent-grid",
      "No observations indexed yet. Start the capture loop to build memory.",
    );
    return;
  }

  recentObservations.className = "recent-grid";
  recentObservations.innerHTML = items.map((item) => resultCard(item, { showScore: false })).join("");
}

async function refreshStatus() {
  try {
    const data = await fetchJSON("/api/status");
    renderStatus(data);
  } catch (error) {
    statusChip.innerHTML = `<span class="status-indicator status-indicator-error"></span><span class="status-chip__label">Off</span>`;
    statusChip.classList.remove("running");
    statusChip.title = "Status unavailable";
  }
}

async function loadRecentObservations() {
  try {
    const data = await fetchJSON("/api/observations?limit=8");
    renderRecent(data);
  } catch (_error) {
    setEmptyState(
      recentObservations,
      "recent-grid",
      "Recent observations could not be loaded right now.",
    );
  }
}

async function previewTime() {
  const originalText = previewTimeBtn.textContent;
  previewTimeBtn.textContent = "Parsing...";
  previewTimeBtn.disabled = true;

  try {
    const data = await fetchJSON("/api/tools/extract-time", {
      method: "POST",
      body: JSON.stringify({ text: timeFilterInput.value }),
    });
    renderTimePreview(data);
  } catch (error) {
    timePreview.textContent = `Time extraction failed: ${error.message}`;
  } finally {
    previewTimeBtn.textContent = originalText;
    previewTimeBtn.disabled = false;
  }
}

function setAnswerLoading() {
  answerOutput.classList.remove("empty-answer");
  answerOutput.innerHTML =
    '<p class="answer-loading">Searching screen history and generating a grounded response...</p>';
}

async function runQuery() {
  if (!queryInput.value.trim()) {
    queryInput.focus();
    return;
  }

  runQueryBtn.disabled = true;
  runQueryBtn.textContent = "Thinking...";
  setAnswerLoading();
  cleanedQuery.textContent = "";

  try {
    const data = await fetchJSON("/api/query", {
      method: "POST",
      body: JSON.stringify({
        query: queryInput.value,
        time_filter: timeFilterInput.value || null,
        limit: 8,
      }),
    });

    const rawHTML = marked.parse(data.answer);
    const cleanHTML = DOMPurify.sanitize(rawHTML);
    answerOutput.innerHTML = cleanHTML;

    cleanedQuery.textContent = data.cleaned_query
      ? `Search query: "${data.cleaned_query}"`
      : "";

    renderTimePreview(data.extracted_time);
    renderResults(data.results);
  } catch (error) {
    answerOutput.classList.add("empty-answer");
    answerOutput.innerHTML = `Query failed: ${escapeHtml(error.message)}`;
    renderResults([]);
  } finally {
    runQueryBtn.disabled = false;
    runQueryBtn.textContent = "Ask Rewind";
  }
}

async function invokeCapture(url, buttonId) {
  const button = document.getElementById(buttonId);
  const originalText = button.textContent;
  button.textContent = "Working...";
  button.disabled = true;

  try {
    await fetchJSON(url, { method: "POST" });
    await refreshStatus();
    await loadRecentObservations();
  } catch (error) {
    console.error(`Capture action failed: ${error.message}`);
  } finally {
    button.textContent = originalText;
    button.disabled = false;
  }
}

themeToggle.addEventListener("click", () => {
  const currentTheme = document.documentElement.dataset.theme || getSystemTheme();
  const nextTheme = currentTheme === "dark" ? "light" : "dark";
  localStorage.setItem(THEME_KEY, nextTheme);
  applyTheme(nextTheme);
});

document
  .getElementById("startCapture")
  .addEventListener("click", () => invokeCapture("/api/capture/start", "startCapture"));
document
  .getElementById("stopCapture")
  .addEventListener("click", () => invokeCapture("/api/capture/stop", "stopCapture"));
document
  .getElementById("runCaptureOnce")
  .addEventListener("click", () => invokeCapture("/api/capture/run-once", "runCaptureOnce"));
previewTimeBtn.addEventListener("click", previewTime);
runQueryBtn.addEventListener("click", runQuery);

queryInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    runQuery();
  }
});

timeFilterInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    previewTime();
  }
});

initializeTheme();
actionLoop();

async function actionLoop() {
  await refreshStatus();
  await loadRecentObservations();
  setInterval(refreshStatus, 5000);
  setInterval(loadRecentObservations, 15000);
}
