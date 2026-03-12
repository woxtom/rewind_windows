const statusChip = document.getElementById("statusChip");
const captureStatus = document.getElementById("captureStatus");
const timePreview = document.getElementById("timePreview");
const answerOutput = document.getElementById("answerOutput");
const cleanedQuery = document.getElementById("cleanedQuery");
const results = document.getElementById("results");
const recentObservations = document.getElementById("recentObservations");
const queryInput = document.getElementById("queryInput");
const timeFilterInput = document.getElementById("timeFilterInput");

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
  return date.toLocaleString();
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderStatus(data) {
  statusChip.textContent = data.running ? "Capture loop running" : "Capture loop stopped";
  captureStatus.innerHTML = "";
  const pairs = [
    ["Interval", `${data.interval_seconds}s`],
    ["Last run started", formatDateTime(data.last_run_started_at)],
    ["Last run completed", formatDateTime(data.last_run_completed_at)],
    ["Last error", data.last_error || "-"],
    ["Windows seen", data.stats.windows_seen ?? 0],
    ["Inserted", data.stats.observations_inserted ?? 0],
    ["Extended", data.stats.observations_extended ?? 0],
    ["Transcription failures", data.stats.transcription_failures ?? 0],
    ["Total observations", data.stats.total_observations ?? 0],
  ];

  for (const [key, value] of pairs) {
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value;
    captureStatus.append(dt, dd);
  }
}

function renderTimePreview(data) {
  if (!data || (!data.start && !data.end)) {
    timePreview.textContent = "No time filter parsed yet.";
    return;
  }

  timePreview.textContent = [
    `Parsed range: ${formatDateTime(data.start)} → ${formatDateTime(data.end)}`,
    data.matched_text ? `matched "${data.matched_text}"` : null,
    data.query_without_time ? `remaining query: "${data.query_without_time}"` : null,
  ]
    .filter(Boolean)
    .join(" | ");
}

function resultCard(item) {
  return `
    <article class="card">
      <h3>${escapeHtml(item.window_title)}</h3>
      <div class="scores">
        <span>PID ${escapeHtml(item.pid)}</span>
        <span>${escapeHtml(formatDateTime(item.first_seen_at))}</span>
        <span>→</span>
        <span>${escapeHtml(formatDateTime(item.last_seen_at))}</span>
        <span>${escapeHtml(item.capture_count)} captures</span>
      </div>
      <div class="scores">
        <span>score: ${item.score ?? "-"}</span>
        <span>vector: ${item.vector_score ?? "-"}</span>
        <span>keyword: ${item.keyword_score ?? "-"}</span>
      </div>
      <img src="${escapeHtml(item.screenshot_url)}" alt="Screenshot for ${escapeHtml(item.window_title)}" loading="lazy" />
      <details>
        <summary>Markdown</summary>
        <pre>${escapeHtml(item.markdown)}</pre>
      </details>
      <details>
        <summary>Notes</summary>
        <pre>${escapeHtml(item.notes || "-")}</pre>
      </details>
    </article>
  `;
}

function renderResults(items) {
  if (!items || items.length === 0) {
    results.className = "card-grid empty-state";
    results.textContent = "No retrieval results.";
    return;
  }
  results.className = "card-grid";
  results.innerHTML = items.map(resultCard).join("");
}

function renderRecent(items) {
  if (!items || items.length === 0) {
    recentObservations.className = "card-grid empty-state";
    recentObservations.textContent = "No observations indexed yet.";
    return;
  }
  recentObservations.className = "card-grid";
  recentObservations.innerHTML = items.map(resultCard).join("");
}

async function refreshStatus() {
  try {
    const data = await fetchJSON("/api/status");
    renderStatus(data);
  } catch (error) {
    statusChip.textContent = `Status error: ${error.message}`;
  }
}

async function loadRecentObservations() {
  try {
    const data = await fetchJSON("/api/observations?limit=8");
    renderRecent(data);
  } catch (error) {
    recentObservations.className = "card-grid empty-state";
    recentObservations.textContent = `Could not load observations: ${error.message}`;
  }
}

async function previewTime() {
  try {
    const data = await fetchJSON("/api/tools/extract-time", {
      method: "POST",
      body: JSON.stringify({ text: timeFilterInput.value }),
    });
    renderTimePreview(data);
  } catch (error) {
    timePreview.textContent = `Time extraction failed: ${error.message}`;
  }
}

async function runQuery() {
  answerOutput.textContent = "Running retrieval and final answer generation…";
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
    answerOutput.textContent = data.answer;
    cleanedQuery.textContent = data.cleaned_query
      ? `Semantic retrieval query: ${data.cleaned_query}`
      : "";
    renderTimePreview(data.extracted_time);
    renderResults(data.results);
  } catch (error) {
    answerOutput.textContent = `Query failed: ${error.message}`;
  }
}

async function invokeCapture(url) {
  try {
    await fetchJSON(url, { method: "POST" });
    await refreshStatus();
    await loadRecentObservations();
  } catch (error) {
    statusChip.textContent = `Capture action failed: ${error.message}`;
  }
}

document.getElementById("startCapture").addEventListener("click", () => invokeCapture("/api/capture/start"));
document.getElementById("stopCapture").addEventListener("click", () => invokeCapture("/api/capture/stop"));
document.getElementById("runCaptureOnce").addEventListener("click", () => invokeCapture("/api/capture/run-once"));
document.getElementById("previewTime").addEventListener("click", previewTime);
document.getElementById("runQuery").addEventListener("click", runQuery);

actionLoop();

async function actionLoop() {
  await refreshStatus();
  await loadRecentObservations();
  setInterval(refreshStatus, 5000);
  setInterval(loadRecentObservations, 15000);
}
