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
const profiles = document.querySelectorAll("[data-demo-key]");

let history = [];
let conversationKey = "";
let pending = false;

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
  for (const control of [keyInput, model, messageInput, newChat, send, ...profiles]) {
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
for (const button of profiles) {
  button.addEventListener("click", () => {
    keyInput.value = button.dataset.demoKey;
    syncKey();
  });
}
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
    setError("Enter an application key or choose a demo application.");
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
  setPending(true);
  statusLine.textContent = "Waiting for the gateway…";

  try {
    const response = await fetch("/v1/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      body: JSON.stringify({ model: requestedModel, messages: [...history, userMessage] }),
      cache: "no-store",
      redirect: "error",
    });
    const result = await response.json().catch(() => null);
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
    setError("Could not reach the gateway. Check that the local stack is running.");
  } finally {
    if (errorLine.textContent) statusLine.textContent = "";
    setPending(false);
    messageInput.focus();
  }
});
