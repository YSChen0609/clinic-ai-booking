(() => {
  const toggle = document.getElementById("chat-toggle");
  const panel = document.getElementById("chat-panel");
  const closeBtn = document.getElementById("chat-close");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const messages = document.getElementById("chat-messages");
  const faqChips = document.getElementById("chat-faq-chips");
  const sendBtn = form ? form.querySelector("button[type='submit']") : null;
  const chipButtons = faqChips
    ? Array.from(faqChips.querySelectorAll("[data-faq]"))
    : [];

  if (!toggle || !panel || !closeBtn || !form || !input || !messages) {
    return;
  }

  let busy = false;
  let historyLoaded = false;

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      input.focus();
      loadHistory();
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

  function clearBubbles() {
    Array.from(messages.querySelectorAll(".chat-bubble")).forEach((node) => {
      node.remove();
    });
  }

  function setBusy(next) {
    busy = next;
    input.disabled = next;
    if (sendBtn) {
      sendBtn.disabled = next;
    }
    chipButtons.forEach((btn) => {
      btn.disabled = next;
    });
  }

  const FAQ_USER_LABELS = new Set([
    "What services do you offer?",
    "Who are the doctors?",
    "What are your hours?",
  ]);

  function hideFaqChips() {
    if (faqChips) {
      faqChips.hidden = true;
    }
  }

  function showFaqChips() {
    if (faqChips) {
      faqChips.hidden = false;
    }
  }

  function historyHasNaturalLanguage(rows) {
    return rows.some(
      (row) =>
        row &&
        row.role === "user" &&
        typeof row.text === "string" &&
        row.text.trim() &&
        !FAQ_USER_LABELS.has(row.text.trim())
    );
  }

  async function loadHistory() {
    if (historyLoaded) {
      return;
    }
    historyLoaded = true;
    try {
      const response = await fetch("/api/chat/history", {
        method: "GET",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      const rows = payload && Array.isArray(payload.messages) ? payload.messages : [];
      if (!rows.length) {
        showFaqChips();
        return;
      }
      clearBubbles();
      rows.forEach((row) => {
        if (row && (row.role === "user" || row.role === "bot") && row.text) {
          appendBubble(row.role, row.text);
        }
      });
      // Keep FAQ chips after chip-only browsing; hide once NL chat started.
      if (historyHasNaturalLanguage(rows)) {
        hideFaqChips();
      } else {
        showFaqChips();
      }
    } catch (err) {
      console.error("chat history failed", err);
    }
  }

  toggle.addEventListener("click", () => {
    setOpen(panel.hidden);
  });

  closeBtn.addEventListener("click", () => {
    setOpen(false);
  });

  async function sendChat(text) {
    appendBubble("user", text);
    setBusy(true);
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
      setBusy(false);
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  }

  async function sendFaq(kind) {
    if (busy) {
      return;
    }
    setBusy(true);
    const pendingUser = appendBubble("user", "…");
    const pendingBot = appendBubble("bot", "…");

    try {
      const response = await fetch("/api/faq", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ kind }),
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
      if (!response.ok) {
        pendingUser.textContent = "Quick question";
        pendingBot.textContent =
          payload && typeof payload.detail === "string"
            ? payload.detail
            : "Sorry — FAQ failed.";
        return;
      }
      pendingUser.textContent =
        payload && typeof payload.label === "string"
          ? payload.label
          : "Quick question";
      pendingBot.textContent =
        payload && typeof payload.reply === "string"
          ? payload.reply
          : "Sorry — empty FAQ reply.";
      // Keep chips so the visitor can open another FAQ before typing.
    } catch (err) {
      console.error("FAQ request failed", err);
      pendingUser.textContent = "Quick question";
      pendingBot.textContent = "Sorry — could not reach the FAQ service.";
    } finally {
      setBusy(false);
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  }

  chipButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (btn.disabled || busy) {
        return;
      }
      const kind = btn.getAttribute("data-faq");
      if (kind === "services" || kind === "professionals" || kind === "hours") {
        sendFaq(kind);
      }
    });
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) {
      return;
    }
    input.value = "";
    hideFaqChips();
    await sendChat(text);
  });

  function isBrowserRefresh() {
    const entries = performance.getEntriesByType("navigation");
    if (entries.length && entries[0].type) {
      return entries[0].type === "reload";
    }
    // Legacy fallback (Safari older): 1 === TYPE_RELOAD
    if (performance.navigation && typeof performance.navigation.type === "number") {
      return performance.navigation.type === 1;
    }
    return false;
  }

  async function bootstrapChat() {
    // Refresh = new chat session. In-tab link navigation keeps transcript.
    if (isBrowserRefresh()) {
      historyLoaded = true;
      try {
        await fetch("/api/chat/reset", {
          method: "POST",
          headers: { Accept: "application/json" },
          credentials: "same-origin",
        });
      } catch (err) {
        console.error("chat reset failed", err);
      }
      return;
    }
    await loadHistory();
  }

  bootstrapChat();
})();
