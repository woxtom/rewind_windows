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

// Configure Marked to support breaks and GitHub Flavored Markdown
marked.use({ breaks: true, gfm: true });

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
  // Make the date output slightly cleaner
  return date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'
  });
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
  statusChip.innerHTML = `<span class="status-indicator"></span> ${data.running ? "Loop Running" : "Loop Stopped"}`;
  if (data.running) {
    statusChip.classList.add("running");
  } else {
    statusChip.classList.remove("running");
  }

  captureStatus.innerHTML = "";
  const pairs = [
    ["Interval", `${data.interval_seconds}s`],
    ["Last run", formatDateTime(data.last_run_started_at)],
    ["Windows seen", data.stats.windows_seen ?? 0],
    ["Observations", data.stats.total_observations ?? 0],
    ["Inserted", data.stats.observations_inserted ?? 0],
    ["Last error", data.last_error || "None"],
  ];

  for (const [key, value] of pairs) {
    const wrapper = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = key;
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrapper.append(dt, dd);
    captureStatus.append(wrapper);
  }
}

function renderTimePreview(data) {
  if (!data || (!data.start && !data.end)) {
    timePreview.textContent = "No time filter applied.";
    return;
  }

  timePreview.innerHTML = `<strong>Active Filter:</strong> ` + [
    `${formatDateTime(data.start)} → ${formatDateTime(data.end)}`,
    data.matched_text ? `(Matched: "${data.matched_text}")` : null,
  ]
    .filter(Boolean)
    .join(" | ");
}

function resultCard(item) {
  return `
    <article class="card">
      <h3 title="${escapeHtml(item.window_title)}">${escapeHtml(item.window_title)}</h3>
      
      <div class="badges">
        <span class="badge" title="Process ID">PID: ${escapeHtml(item.pid)}</span>
        <span class="badge" title="Score (Vector/Keyword)">Score: ${Number(item.score).toFixed(2) || "-"}</span>
        <span class="badge">${escapeHtml(item.capture_count)} captures</span>
      </div>
      
      <div class="meta-line" style="margin-bottom: 0;">
        ${escapeHtml(formatDateTime(item.first_seen_at))} → ${escapeHtml(formatDateTime(item.last_seen_at))}
      </div>
      
      <img src="${escapeHtml(item.screenshot_url)}" alt="Screenshot" loading="lazy" />
      
      <details>
        <summary>Extracted Markdown</summary>
        <pre>${escapeHtml(item.markdown)}</pre>
      </details>
      ${item.notes ? `
      <details>
        <summary>Notes</summary>
        <pre>${escapeHtml(item.notes)}</pre>
      </details>` : ""}
    </article>
  `;
}

function renderResults(items) {
  if (!items || items.length === 0) {
    results.className = "card-grid empty-state";
    results.textContent = "No retrieval results match your query.";
    return;
  }
  results.className = "card-grid";
  results.innerHTML = items.map(resultCard).join("");
}

function renderRecent(items) {
  if (!items || items.length === 0) {
    recentObservations.className = "card-grid empty-state";
    recentObservations.textContent = "No observations indexed yet. Start the capture loop!";
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
    statusChip.innerHTML = `<span class="status-indicator" style="background: #ef4444;"></span> Error`;
  }
}

async function loadRecentObservations() {
  try {
    const data = await fetchJSON("/api/observations?limit=8");
    renderRecent(data);
  } catch (error) {
    // Silently fail or update UI
  }
}

async function previewTime() {
  const btn = document.getElementById("previewTime");
  const originalText = btn.textContent;
  btn.textContent = "⏳...";
  
  try {
    const data = await fetchJSON("/api/tools/extract-time", {
      method: "POST",
      body: JSON.stringify({ text: timeFilterInput.value }),
    });
    renderTimePreview(data);
  } catch (error) {
    timePreview.textContent = `Time extraction failed: ${error.message}`;
  } finally {
    btn.textContent = originalText;
  }
}

async function runQuery() {
  if (!queryInput.value.trim()) {
    queryInput.focus();
    return;
  }

  // UI Loading State
  runQueryBtn.disabled = true;
  runQueryBtn.innerHTML = "⏳ Thinking...";
  answerOutput.classList.remove("empty-answer");
  answerOutput.innerHTML = `<p style="color: #94a3b8; animation: pulse 1.5s infinite;">Searching screen history and generating response...</p>`;
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
    
    // Parse Markdown and Sanitize HTML output
    const rawHTML = marked.parse(data.answer);
    const cleanHTML = DOMPurify.sanitize(rawHTML);
    answerOutput.innerHTML = cleanHTML;

    cleanedQuery.textContent = data.cleaned_query
      ? `Search Query used: "${data.cleaned_query}"`
      : "";
      
    renderTimePreview(data.extracted_time);
    renderResults(data.results);
    
  } catch (error) {
    answerOutput.classList.add("empty-answer");
    answerOutput.innerHTML = `Query failed: ${escapeHtml(error.message)}`;
  } finally {
    runQueryBtn.disabled = false;
    runQueryBtn.innerHTML = "✨ Ask Rewind";
  }
}

async function invokeCapture(url, btnId) {
  const btn = document.getElementById(btnId);
  const originalText = btn.textContent;
  btn.textContent = "⏳...";
  btn.disabled = true;

  try {
    await fetchJSON(url, { method: "POST" });
    await refreshStatus();
    await loadRecentObservations();
  } catch (error) {
    console.error(`Capture action failed: ${error.message}`);
  } finally {
    btn.textContent = originalText;
    btn.disabled = false;
  }
}

// Event Listeners
document.getElementById("startCapture").addEventListener("click", () => invokeCapture("/api/capture/start", "startCapture"));
document.getElementById("stopCapture").addEventListener("click", () => invokeCapture("/api/capture/stop", "stopCapture"));
document.getElementById("runCaptureOnce").addEventListener("click", () => invokeCapture("/api/capture/run-once", "runCaptureOnce"));
document.getElementById("previewTime").addEventListener("click", previewTime);
document.getElementById("runQuery").addEventListener("click", runQuery);

// Allow pressing 'Enter' inside query to run
queryInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    runQuery();
  }
});

timeFilterInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    previewTime();
  }
});

// Initialize Loop
actionLoop();

async function actionLoop() {
  await refreshStatus();
  await loadRecentObservations();
  setInterval(refreshStatus, 5000);
  setInterval(loadRecentObservations, 15000);
}