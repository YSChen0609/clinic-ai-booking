(() => {
  const toggle = document.getElementById("chat-toggle");
  const panel = document.getElementById("chat-panel");
  const closeBtn = document.getElementById("chat-close");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const messages = document.getElementById("chat-messages");
  const sendBtn = form ? form.querySelector("button[type='submit']") : null;

  if (!toggle || !panel || !closeBtn || !form || !input || !messages) {
    return;
  }

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      input.focus();
    }
  }

  function appendBubble(role, text) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;
    bubble.textContent = text;
    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return bubble;
  }

  toggle.addEventListener("click", () => {
    setOpen(panel.hidden);
  });

  closeBtn.addEventListener("click", () => {
    setOpen(false);
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) {
      return;
    }

    appendBubble("user", text);
    input.value = "";
    input.disabled = true;
    if (sendBtn) {
      sendBtn.disabled = true;
    }

    const pending = appendBubble("bot", "Thinking…");

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ message: text }),
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const detail =
          payload && typeof payload.detail === "string"
            ? payload.detail
            : "Sorry — chat failed. Please try again.";
        pending.textContent = detail;
        return;
      }
      pending.textContent =
        payload && typeof payload.reply === "string"
          ? payload.reply
          : "Sorry — empty reply.";
    } catch {
      pending.textContent = "Sorry — could not reach the chat service.";
    } finally {
      input.disabled = false;
      if (sendBtn) {
        sendBtn.disabled = false;
      }
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  });
})();
