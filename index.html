const API_BASE =
  window.location.hostname === "127.0.0.1" || window.location.hostname === "localhost"
    ? "http://127.0.0.1:8000"
    : "";

const messages = document.getElementById("messages");
const form = document.getElementById("chatForm");
const input = document.getElementById("questionInput");
const sendButton = document.getElementById("sendButton");
const userTemplate = document.getElementById("userMessageTemplate");
const assistantTemplate = document.getElementById("assistantMessageTemplate");
const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatAnswer(text) {
  const escaped = escapeHtml(text);

  // Lightweight markdown support for bold and simple bullet lists.
  const lines = escaped.split("\n");
  let html = "";
  let inList = false;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (!line) {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      continue;
    }

    const bolded = line.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");

    if (bolded.startsWith("- ")) {
      if (!inList) {
        html += "<ul>";
        inList = true;
      }
      html += `<li>${bolded.slice(2)}</li>`;
    } else {
      if (inList) {
        html += "</ul>";
        inList = false;
      }
      html += `<p>${bolded}</p>`;
    }
  }

  if (inList) html += "</ul>";
  return html;
}

function addUserMessage(text) {
  const node = userTemplate.content.cloneNode(true);
  node.querySelector(".user-message").textContent = text;
  messages.appendChild(node);
}

function addLoadingMessage() {
  const node = assistantTemplate.content.cloneNode(true);
  const card = node.querySelector(".assistant-card");
  card.classList.add("loading-card");
  node.querySelector(".answer").textContent = "Searching planning sources and checking the answer…";
  node.querySelector(".sources-panel").remove();
  messages.appendChild(node);
  return messages.lastElementChild;
}

function addAssistantMessage(answer, sources) {
  const node = assistantTemplate.content.cloneNode(true);
  node.querySelector(".answer").innerHTML = formatAnswer(answer);

  const list = node.querySelector(".sources-list");
  const unique = [];
  const seen = new Set();

  for (const source of sources || []) {
    const key = `${source.title}|${source.page_number}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(source);
  }

  for (const source of unique) {
    const card = document.createElement("div");
    card.className = "source-card";

    const title = document.createElement("div");
    title.className = "source-title";
    title.textContent = source.title;

    const meta = document.createElement("div");
    meta.className = "source-meta";

    const parts = [];
    if (source.page_number != null) parts.push(`PDF page ${source.page_number}`);
    if (source.section_title) parts.push(source.section_title);
    if (source.publication_date) parts.push(source.publication_date);
    meta.textContent = parts.join(" · ");

    card.appendChild(title);
    card.appendChild(meta);
    list.appendChild(card);
  }

  if (unique.length === 0) {
    node.querySelector(".sources-panel").remove();
  }

  messages.appendChild(node);
}

function addErrorMessage(message) {
  const node = assistantTemplate.content.cloneNode(true);
  const card = node.querySelector(".assistant-card");
  card.classList.add("error-card");
  node.querySelector(".answer").textContent = message;
  node.querySelector(".sources-panel").remove();
  messages.appendChild(node);
}

function scrollToBottom() {
  window.scrollTo({
    top: document.body.scrollHeight,
    behavior: "smooth",
  });
}

async function checkApi() {
  try {
    const response = await fetch(`${API_BASE}/health`);
    if (!response.ok) throw new Error("API unavailable");

    const data = await response.json();
    statusDot.className = "status-dot online";
    statusText.textContent = `Online · ${data.chunks_loaded} chunks`;
  } catch {
    statusDot.className = "status-dot offline";
    statusText.textContent = "API offline";
  }
}

async function submitQuestion(question) {
  const trimmed = question.trim();
  if (!trimmed) return;

  const welcome = document.querySelector(".welcome-card");
  if (welcome) welcome.remove();

  addUserMessage(trimmed);
  input.value = "";
  resizeInput();
  sendButton.disabled = true;
  scrollToBottom();

  const loadingNode = addLoadingMessage();
  scrollToBottom();

  try {
    const response = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ question: trimmed }),
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "The API returned an error.");
    }

    loadingNode.remove();
    addAssistantMessage(data.answer, data.sources);
  } catch (error) {
    loadingNode.remove();
    addErrorMessage(
      `Could not get an answer. Make sure the API is running on ${API_BASE}. ${error.message}`
    );
  } finally {
    sendButton.disabled = false;
    input.focus();
    scrollToBottom();
  }
}

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  submitQuestion(input.value);
});

input.addEventListener("input", resizeInput);

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

document.querySelectorAll(".example-btn").forEach((button) => {
  button.addEventListener("click", () => submitQuestion(button.textContent));
});

checkApi();
setInterval(checkApi, 15000);
input.focus();
