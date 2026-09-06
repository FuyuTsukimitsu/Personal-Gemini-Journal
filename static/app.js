import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";

// ---------------------------------------------------------------------------
// Firebase init — config is fetched from our own backend, which reads it
// from env vars. This is public config (not a secret); access is enforced
// by Firebase Auth + Firestore rules, not by hiding these values.
// ---------------------------------------------------------------------------
const firebaseConfig = await fetch("/config.json").then((r) => r.json());
const firebaseApp = initializeApp(firebaseConfig);
const auth = getAuth(firebaseApp);
const provider = new GoogleAuthProvider();

const el = (id) => document.getElementById(id);

const landing = el("landing");
const appRoot = el("app");
const signInBtn = el("sign-in-btn");
const signOutBtn = el("sign-out-btn");
const landingError = el("landing-error");
const userPhoto = el("user-photo");
const userName = el("user-name");
const sessionListEl = el("session-list");
const newEntryBtn = el("new-entry-btn");
const digestBtn = el("digest-btn");
const searchInput = el("search-input");
const exportBtn = el("export-btn");
const entryEmpty = el("entry-empty");
const onThisDay = el("on-this-day");
const onThisDayText = el("on-this-day-text");
const onThisDayOpen = el("on-this-day-open");
const entryView = el("entry-view");
const entryTitle = el("entry-title");
const entrySummary = el("entry-summary");
const entrySummaryText = el("entry-summary-text");
const summaryToggle = el("summary-toggle");
const summarizeBtn = el("summarize-btn");
const deleteBtn = el("delete-btn");
const moodPicker = el("mood-picker");
const thread = el("thread");
const composer = el("composer");
const composerInput = el("composer-input");
const composerSend = el("composer-send");
const digestView = el("digest-view");
const digestContent = el("digest-content");
const sidebarToggle = el("sidebar-toggle");
const sidebarBackdrop = el("sidebar-backdrop");
const sidebar = el("sidebar");
const sidebarClose = el("sidebar-close");

const MOODS = ["😊", "😐", "😔", "😤", "✨"];

function openSidebar() {
  sidebar.classList.add("open");
  sidebarBackdrop.classList.add("visible");
  sidebarToggle.hidden = true;
}
function closeSidebar() {
  sidebar.classList.remove("open");
  sidebarBackdrop.classList.remove("visible");
  sidebarToggle.hidden = false;
}
sidebarToggle.addEventListener("click", openSidebar);
sidebarBackdrop.addEventListener("click", closeSidebar);
sidebarClose.addEventListener("click", closeSidebar);

function showSummary(text) {
  entrySummaryText.textContent = text;
  entrySummary.hidden = false;
  entrySummary.classList.remove("collapsed");
  summaryToggle.textContent = "Hide";
}

summaryToggle.addEventListener("click", () => {
  const collapsed = entrySummary.classList.toggle("collapsed");
  summaryToggle.textContent = collapsed ? "Show" : "Hide";
});

composerInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    composer.requestSubmit();
  }
});

let currentUser = null;
let currentSessionId = null;
let sessions = [];

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
signInBtn.addEventListener("click", async () => {
  landingError.hidden = true;
  try {
    await signInWithPopup(auth, provider);
  } catch (err) {
    landingError.textContent = "Sign-in didn't go through. Please try again.";
    landingError.hidden = false;
  }
});

signOutBtn.addEventListener("click", () => signOut(auth));

onAuthStateChanged(auth, async (user) => {
  currentUser = user;
  if (user) {
    landing.hidden = true;
    appRoot.hidden = false;
    userPhoto.src = user.photoURL || "";
    userName.textContent = user.displayName || user.email || "Signed in";
    await refreshSessions();
    checkOnThisDay();
  } else {
    appRoot.hidden = true;
    landing.hidden = false;
    currentSessionId = null;
  }
});

async function authedFetch(path, options = {}) {
  const token = await currentUser.getIdToken();
  const res = await fetch(path, {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: `Bearer ${token}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------
async function refreshSessions() {
  sessions = await authedFetch("/api/sessions");
  renderSessionList();
}

function renderSessionList() {
  const query = searchInput.value.trim().toLowerCase();
  const visible = query
    ? sessions.filter(
        (s) =>
          (s.title || "").toLowerCase().includes(query) ||
          (s.summary || "").toLowerCase().includes(query)
      )
    : sessions;

  sessionListEl.innerHTML = "";
  if (query && visible.length === 0) {
    const empty = document.createElement("p");
    empty.className = "session-item-date";
    empty.style.padding = "12px 20px";
    empty.textContent = "No entries match your search.";
    sessionListEl.appendChild(empty);
    return;
  }

  for (const s of visible) {
    const btn = document.createElement("button");
    btn.className = "session-item" + (s.id === currentSessionId ? " active" : "");
    btn.innerHTML = `
      <p class="session-item-title"></p>
      <p class="session-item-date"></p>
    `;
    const titleEl = btn.querySelector(".session-item-title");
    if (s.mood) {
      const moodSpan = document.createElement("span");
      moodSpan.className = "session-item-mood";
      moodSpan.textContent = s.mood;
      titleEl.appendChild(moodSpan);
    }
    titleEl.append(s.title);
    btn.querySelector(".session-item-date").textContent = formatDate(s.updatedAt);
    btn.addEventListener("click", () => {
      openSession(s.id);
      closeSidebar();
    });
    sessionListEl.appendChild(btn);
  }
}

searchInput.addEventListener("input", renderSessionList);

function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

newEntryBtn.addEventListener("click", async () => {
  const { id } = await authedFetch("/api/sessions", { method: "POST", body: "{}" });
  await refreshSessions();
  await openSession(id);
  checkOnThisDay();
  closeSidebar();
});

async function checkOnThisDay() {
  try {
    const result = await authedFetch("/api/on-this-day");
    if (result.found) {
      const phrase = result.session.daysAgo <= 10 ? "A week ago" : "A month ago";
      onThisDayText.textContent = `${phrase} you wrote "${result.session.title}"${
        result.session.summary ? " — " + result.session.summary : ""
      }`;
      onThisDayOpen.onclick = () => openSession(result.session.id);
      onThisDay.hidden = false;
    } else {
      onThisDay.hidden = true;
    }
  } catch {
    onThisDay.hidden = true;
  }
}

async function openSession(id) {
  currentSessionId = id;
  digestView.hidden = true;
  onThisDay.hidden = true;
  renderSessionList();
  entryEmpty.hidden = true;
  entryView.hidden = false;

  const s = sessions.find((s) => s.id === id);
  entryTitle.textContent = s ? s.title : "Untitled entry";
  renderMoodPicker(s ? s.mood : null);

  if (s && s.summary) {
    showSummary(s.summary);
  } else {
    entrySummary.hidden = true;
  }

  const messages = await authedFetch(`/api/sessions/${id}/messages`);
  renderThread(messages);
  composerInput.focus();
}

function renderMoodPicker(selectedMood) {
  moodPicker.innerHTML = "";
  for (const mood of MOODS) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "mood-btn" + (mood === selectedMood ? " selected" : "");
    btn.textContent = mood;
    btn.title = "Set mood";
    btn.addEventListener("click", () => setMood(mood));
    moodPicker.appendChild(btn);
  }
}

async function setMood(mood) {
  if (!currentSessionId) return;
  try {
    await authedFetch(`/api/sessions/${currentSessionId}/mood`, {
      method: "PATCH",
      body: JSON.stringify({ mood }),
    });
    renderMoodPicker(mood);
    await refreshSessions();
    renderSessionList();
  } catch {
    // Non-critical — fail quietly, the picker just won't update.
  }
}

// ---------------------------------------------------------------------------
// Rename (click-to-edit title)
// ---------------------------------------------------------------------------
entryTitle.addEventListener("click", () => {
  if (!currentSessionId) return;
  const currentText = entryTitle.textContent;

  const input = document.createElement("input");
  input.type = "text";
  input.className = "entry-title-input";
  input.value = currentText;
  entryTitle.replaceWith(input);
  input.focus();
  input.select();

  const commit = async () => {
    const newTitle = input.value.trim() || currentText;
    input.replaceWith(entryTitle);
    entryTitle.textContent = newTitle;
    if (newTitle !== currentText) {
      try {
        await authedFetch(`/api/sessions/${currentSessionId}/title`, {
          method: "PATCH",
          body: JSON.stringify({ title: newTitle }),
        });
        await refreshSessions();
      } catch {
        entryTitle.textContent = currentText;
      }
    }
  };

  input.addEventListener("blur", commit);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      input.blur();
    } else if (e.key === "Escape") {
      input.value = currentText;
      input.blur();
    }
  });
});

function renderThread(messages) {
  thread.innerHTML = "";
  for (const m of messages) {
    const node = document.createElement("div");
    if (m.role === "user") {
      node.className = "msg-user";
      node.textContent = m.content;
    } else {
      node.className = "msg-model";
      node.innerHTML = `<span class="msg-model-label">Gemini reflects</span>`;
      node.append(m.content);
    }
    thread.appendChild(node);
  }
  thread.scrollTop = thread.scrollHeight;
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------
composer.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = composerInput.value.trim();
  if (!content || !currentSessionId) return;

  composerSend.disabled = true;

  const userNode = document.createElement("div");
  userNode.className = "msg-user";
  userNode.textContent = content;
  thread.appendChild(userNode);
  composerInput.value = "";
  thread.scrollTop = thread.scrollHeight;

  try {
    const reply = await authedFetch(`/api/sessions/${currentSessionId}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    const modelNode = document.createElement("div");
    modelNode.className = "msg-model";
    modelNode.innerHTML = `<span class="msg-model-label">Gemini reflects</span>`;
    modelNode.append(reply.content);
    thread.appendChild(modelNode);
    thread.scrollTop = thread.scrollHeight;
    await refreshSessions();
    renderSessionList();
    const s = sessions.find((s) => s.id === currentSessionId);
    if (s) entryTitle.textContent = s.title;
    if (reply.summary) {
      showSummary(reply.summary);
    }
  } catch (err) {
    const errNode = document.createElement("div");
    errNode.className = "msg-model";
    errNode.textContent = `That reflection didn't come through: ${err.message}`;
    thread.appendChild(errNode);
  } finally {
    composerSend.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Summarize
// ---------------------------------------------------------------------------
summarizeBtn.addEventListener("click", async () => {
  if (!currentSessionId) return;
  summarizeBtn.disabled = true;
  summarizeBtn.textContent = "Summarizing…";
  try {
    const { summary } = await authedFetch(`/api/sessions/${currentSessionId}/summarize`, {
      method: "POST",
      body: "{}",
    });
    showSummary(summary);
    await refreshSessions();
  } catch (err) {
    showSummary("Couldn't summarize this entry yet — try adding a bit more first.");
  } finally {
    summarizeBtn.disabled = false;
    summarizeBtn.textContent = "Summarize this entry";
  }
});

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------
deleteBtn.addEventListener("click", async () => {
  if (!currentSessionId) return;
  const ok = window.confirm("Delete this entry permanently? This can't be undone.");
  if (!ok) return;

  const idToDelete = currentSessionId;
  try {
    await authedFetch(`/api/sessions/${idToDelete}`, { method: "DELETE" });
    currentSessionId = null;
    entryView.hidden = true;
    entryEmpty.hidden = false;
    await refreshSessions();
  } catch (err) {
    window.alert("Couldn't delete this entry — please try again.");
  }
});

// ---------------------------------------------------------------------------
// Weekly digest
// ---------------------------------------------------------------------------
digestBtn.addEventListener("click", async () => {
  currentSessionId = null;
  renderSessionList();
  entryEmpty.hidden = true;
  entryView.hidden = true;
  onThisDay.hidden = true;
  digestView.hidden = false;
  closeSidebar();
  digestContent.textContent = "Reading through this week's entries…";

  try {
    const { content } = await authedFetch("/api/weekly-digest", { method: "POST", body: "{}" });
    digestContent.textContent = content;
  } catch (err) {
    digestContent.textContent =
      err.message === "No entries from the past week yet"
        ? "No entries from the past week yet — write one to get a digest."
        : "Couldn't put together this week's digest — please try again.";
  }
});

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------
exportBtn.addEventListener("click", async () => {
  const token = await currentUser.getIdToken();
  const res = await fetch("/api/export", { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "journal-export.txt";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});
