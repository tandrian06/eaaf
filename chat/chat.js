const keyInput = document.querySelector("#application-key");
const model = document.querySelector("#model");
const form = document.querySelector("#composer");
const messageInput = document.querySelector("#message");
const transcript = document.querySelector("#messages");
const emptyState = document.querySelector("#empty-state");
const statusLine = document.querySelector("#status");
const errorLine = document.querySelector("#error");
const newChat = document.querySelector("#new-chat");
const send = document.querySelector("#send");
const spacePath = window.location.pathname;
for (const link of document.querySelectorAll(".spaces a")) {
  if (link.getAttribute("href") === spacePath) {
    link.setAttribute("aria-current", "page");
    document.querySelector("#chat-title").textContent = `${link.textContent} Chat`;
  }
}
window.addEventListener("pagehide", () => {
  activeRequest?.abort();
  keyInput.value = "";
  syncKey();
});

let history = [];
let conversationKey = "";
let pending = false;
let activeRequest = null;

function setError(message = "") {
  errorLine.hidden = !message;
  errorLine.textContent = message;
}

function clearConversation() {
  history = [];
  transcript.replaceChildren();
  emptyState.hidden = false;
  statusLine.textContent = "";
  setError();
}

function syncKey() {
  const key = keyInput.value.trim();
  if (key !== conversationKey) {
    conversationKey = key;
    clearConversation();
    messageInput.value = "";
  }
}

function setPending(value) {
  pending = value;
  for (const control of [keyInput, model, messageInput, newChat, send]) {
    control.disabled = value;
  }
}

function appendMessage({ role, content }) {
  const item = document.createElement("li");
  item.className = `message ${role}`;
  const heading = document.createElement("strong");
  heading.textContent = role === "user" ? "You" : "Assistant";
  const body = document.createElement("p");
  body.textContent = content;
  item.append(heading, body);
  transcript.append(item);
}

keyInput.addEventListener("input", syncKey);
newChat.addEventListener("click", () => {
  clearConversation();
  messageInput.value = "";
  messageInput.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (pending) return;

  const key = keyInput.value.trim();
  const content = messageInput.value.trim();
  if (key !== conversationKey) {
    conversationKey = key;
    clearConversation();
  } else {
    setError();
    statusLine.textContent = "";
  }
  if (!key) {
    setError("Enter an application key.");
    keyInput.focus();
    return;
  }
  if (!content) {
    setError("Enter a message to send.");
    messageInput.focus();
    return;
  }

  const userMessage = { role: "user", content };
  const requestedModel = model.value;
  const request = new AbortController();
  activeRequest = request;
  setPending(true);
  statusLine.textContent = "Waiting for the gateway…";

  try {
    const response = await fetch(`${spacePath}/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ model: requestedModel, messages: [...history, userMessage] }),
      cache: "no-store",
      redirect: "error",
      signal: request.signal,
    });
    const result = await response.json().catch(() => null);
    if (request.signal.aborted) return;
    if (!response.ok) {
      const detail = typeof result?.detail === "string" ? result.detail : "Gateway request failed";
      setError(`HTTP ${response.status} · ${detail}`);
      return;
    }
    const reply = result?.choices?.[0]?.message?.content;
    if (typeof reply !== "string") {
      setError("The gateway returned an unexpected response.");
      return;
    }
    const assistantMessage = { role: "assistant", content: reply };
    history.push(userMessage, assistantMessage);
    emptyState.hidden = true;
    appendMessage(userMessage);
    appendMessage(assistantMessage);
    messageInput.value = "";
    statusLine.textContent = `HTTP ${response.status} · ${requestedModel}`;
  } catch {
    if (!request.signal.aborted) {
      setError("Could not reach the gateway. Check that the local stack is running.");
    }
  } finally {
    activeRequest = null;
    if (errorLine.textContent) statusLine.textContent = "";
    setPending(false);
    messageInput.focus();
  }
});
