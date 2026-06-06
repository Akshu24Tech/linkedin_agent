/**
 * popup.js
 * ────────
 * Controls the extension popup UI.
 *
 * Flow:
 *   1. Check if local Python server is running (GET /health)
 *   2. Get current tab URL to show context
 *   3. On "Scan" click → send message to content_script → get posts
 *   4. POST posts to /analyze on local server
 *   5. Show results (score, saved/skipped, Notion status)
 */

const SERVER = "http://localhost:8765";

// ── Elements ──────────────────────────────────────────────────────────────────

const statusDot  = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const pageUrl    = document.getElementById("pageUrl");
const pageInfo   = document.getElementById("pageInfo");
const scanBtn    = document.getElementById("scanBtn");
const viewLastBtn = document.getElementById("viewLastBtn");
const resultsArea = document.getElementById("resultsArea");
const thresholdInput = document.getElementById("threshold");

// ── Init ──────────────────────────────────────────────────────────────────────

let currentTab = null;
let isLinkedIn = false;

async function init() {
  // Load saved threshold
  const stored = await chrome.storage.local.get("threshold");
  if (stored.threshold) thresholdInput.value = stored.threshold;

  // Get current tab
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTab = tab;
  isLinkedIn = tab.url && tab.url.includes("linkedin.com");

  // Show page URL
  pageUrl.textContent = tab.url || "Unknown";

  if (!isLinkedIn) {
    pageInfo.querySelector(".label").textContent = "⚠️ Not on LinkedIn";
    pageInfo.querySelector(".url").style.color = "#f59e0b";
    const warn = document.createElement("div");
    warn.className = "warning";
    warn.textContent = "Navigate to a LinkedIn profile activity page first.";
    pageInfo.appendChild(warn);
  }

  // Check server health
  await checkServer();

  // Load last results from storage
  loadLastResults();
}

async function checkServer() {
  statusDot.className = "status-dot checking";
  statusText.textContent = "Checking local server...";
  try {
    const res = await fetch(`${SERVER}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      const data = await res.json();
      statusDot.className = "status-dot online";
      statusText.textContent = `Server ready · ${data.posts_in_memory ?? "?"} posts in memory`;
      if (isLinkedIn) {
        scanBtn.disabled = false;
        scanBtn.querySelector("span:last-child").textContent = "Scan This Page";
      } else {
        scanBtn.querySelector("span:last-child").textContent = "Navigate to LinkedIn first";
      }
    } else {
      throw new Error("Server returned " + res.status);
    }
  } catch (e) {
    statusDot.className = "status-dot offline";
    statusText.textContent = "Server offline — run: python server.py";
    showError("Local server is not running.\n\nStart it with:\n  python server.py\n\nMake sure your venv is activated.");
  }
}

// ── Scan ──────────────────────────────────────────────────────────────────────

scanBtn.addEventListener("click", async () => {
  if (!currentTab) return;

  const threshold = parseInt(thresholdInput.value) || 7;
  await chrome.storage.local.set({ threshold });

  // Show loading state
  scanBtn.disabled = true;
  scanBtn.querySelector("span:first-child").textContent = "⏳";
  scanBtn.querySelector("span:last-child").textContent = "Extracting posts...";
  resultsArea.innerHTML = `
    <div class="loading">
      <div class="spinner"></div>
      Reading posts from page...
    </div>`;

  try {
    // Step 1: Extract posts from LinkedIn DOM
    const extractResult = await chrome.tabs.sendMessage(currentTab.id, { action: "extractPosts" });

    if (!extractResult || !extractResult.posts) {
      throw new Error(extractResult?.error || "Could not extract posts. Try refreshing the LinkedIn page.");
    }

    if (extractResult.posts.length === 0) {
      showError("No posts found on this page.\n\nMake sure you are on someone's activity page:\nlinkedin.com/in/username/recent-activity/all/");
      resetScanBtn();
      return;
    }

    // Step 2: Show extraction count, send to server
    scanBtn.querySelector("span:last-child").textContent =
      `Analyzing ${extractResult.posts.length} posts...`;

    resultsArea.innerHTML = `
      <div class="loading">
        <div class="spinner"></div>
        Sending ${extractResult.posts.length} posts to Gemini...
      </div>`;

    // Step 3: POST to local server
    const serverRes = await fetch(`${SERVER}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        posts: extractResult.posts,
        threshold: threshold,
        source_url: extractResult.pageUrl,
      }),
    });

    if (!serverRes.ok) {
      const err = await serverRes.text();
      throw new Error(`Server error ${serverRes.status}: ${err}`);
    }

    const results = await serverRes.json();

    // Step 4: Save results to local storage for "View Last"
    await chrome.storage.local.set({ lastResults: results, lastScanTime: new Date().toISOString() });

    // Step 5: Render results
    renderResults(results);

  } catch (e) {
    showError(e.message || String(e));
  } finally {
    resetScanBtn();
  }
});

// ── View last ─────────────────────────────────────────────────────────────────

viewLastBtn.addEventListener("click", loadLastResults);

async function loadLastResults() {
  const stored = await chrome.storage.local.get(["lastResults", "lastScanTime"]);
  if (stored.lastResults) {
    renderResults(stored.lastResults, stored.lastScanTime);
  }
}

// ── Render ─────────────────────────────────────────────────────────────────────

function renderResults(results, scanTime) {
  if (!results || results.length === 0) {
    resultsArea.innerHTML = `<div class="empty-state">No results yet.<br>Scan a LinkedIn activity page.</div>`;
    return;
  }

  const saved = results.filter(r => r.saved_to_notion);
  const total = results.length;
  const newCount = results.filter(r => !r.already_seen).length;

  let html = "";

  // Stats bar
  if (scanTime) {
    const time = new Date(scanTime).toLocaleTimeString();
    html += `<div style="font-size:10px;color:#666;margin-bottom:8px;">Last scan: ${time}</div>`;
  }
  html += `
    <div class="stats-bar">
      <div class="stat">
        <div class="stat-value">${total}</div>
        <div class="stat-label">Found</div>
      </div>
      <div class="stat">
        <div class="stat-value">${newCount}</div>
        <div class="stat-label">New</div>
      </div>
      <div class="stat">
        <div class="stat-value">${saved.length}</div>
        <div class="stat-label">Saved</div>
      </div>
    </div>`;

  // Per-post cards
  for (const r of results) {
    const score = r.analysis?.relevance_score ?? 0;
    const scoreClass = score >= 7 ? "high" : score >= 5 ? "mid" : "low";
    const cardClass = r.saved_to_notion ? "saved" : "skipped";
    const author = r.post?.author_name || "Unknown";
    const summary = r.analysis?.post_summary || r.skip_reason || "Already seen";
    const statusMsg = r.already_seen
      ? "⏭ Already in memory"
      : r.saved_to_notion
      ? "✅ Saved to Notion"
      : r.skip_reason
      ? `⊘ ${r.skip_reason}`
      : `Score ${score} < threshold`;

    const statusClass = r.saved_to_notion ? "saved-to-notion" : "skipped-msg";

    html += `
      <div class="result-card ${cardClass}">
        <div class="result-header">
          <div class="author-name">${escHtml(author)}</div>
          ${r.already_seen ? "" : `<span class="score-badge ${scoreClass}">${score}/10</span>`}
        </div>
        <div class="result-summary">${escHtml(summary.substring(0, 100))}</div>
        <div class="result-status ${statusClass}">${escHtml(statusMsg)}</div>
      </div>`;
  }

  resultsArea.innerHTML = html;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function showError(msg) {
  resultsArea.innerHTML = `<div class="error-msg">${escHtml(msg).replace(/\n/g, "<br>")}</div>`;
}

function resetScanBtn() {
  scanBtn.disabled = !isLinkedIn;
  scanBtn.querySelector("span:first-child").textContent = "🔍";
  scanBtn.querySelector("span:last-child").textContent = "Scan This Page";
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── Start ─────────────────────────────────────────────────────────────────────

init();
