(() => {
  const toggle = document.getElementById("chat-toggle");
  const panel = document.getElementById("chat-panel");
  const closeBtn = document.getElementById("chat-close");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const messages = document.getElementById("chat-messages");
  const faqChips = document.getElementById("chat-faq-chips");
  const micBtn = document.getElementById("chat-mic");
  const voiceStatus = document.getElementById("chat-voice-status");
  const sendBtn = document.getElementById("chat-send")
    || (form ? form.querySelector("button[type='submit']") : null);
  const chipButtons = faqChips
    ? Array.from(faqChips.querySelectorAll("[data-faq-token]"))
    : [];
  const TAB_CHAT_KEY = "clinic_chat_tab";

  const ICON_MIC =
    '<svg class="chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 14a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v5a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" fill="currentColor"/></svg>';
  const ICON_STOP =
    '<svg class="chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/></svg>';
  const ICON_PLAY =
    '<svg class="chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5v14l11-7z" fill="currentColor"/></svg>';
  const ICON_PAUSE =
    '<svg class="chat-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M7 5h3v14H7zm7 0h3v14h-3z" fill="currentColor"/></svg>';

  if (!toggle || !panel || !closeBtn || !form || !input || !messages) {
    return;
  }

  let busy = false;
  let historyLoaded = false;
  let mediaRecorder = null;
  let recordChunks = [];
  let recording = false;
  let playbackAudio = null;
  let activeSpeakBtn = null;
  const audioUrlCache = new Map();

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      input.focus();
      loadHistory();
    }
  }

  function setVoiceStatus(text) {
    if (!voiceStatus) {
      return;
    }
    if (!text) {
      voiceStatus.hidden = true;
      voiceStatus.textContent = "";
      return;
    }
    voiceStatus.hidden = false;
    voiceStatus.textContent = text;
  }

  function setMicIcon(recordingNow) {
    if (!micBtn) {
      return;
    }
    if (recordingNow) {
      micBtn.innerHTML = ICON_STOP;
      micBtn.dataset.micState = "stop";
      micBtn.setAttribute("aria-label", "Stop recording");
      micBtn.title = "Stop recording";
      return;
    }
    micBtn.innerHTML = ICON_MIC;
    micBtn.dataset.micState = "mic";
    micBtn.setAttribute("aria-label", "Start recording");
    micBtn.title = "Start recording";
  }

  function setSpeakIcon(btn, state) {
    if (!btn) {
      return;
    }
    if (state === "pause") {
      btn.innerHTML = ICON_PAUSE;
      btn.dataset.speakState = "pause";
      btn.setAttribute("aria-label", "Pause reply");
      btn.title = "Pause reply";
      return;
    }
    btn.innerHTML = ICON_PLAY;
    btn.dataset.speakState = "play";
    btn.setAttribute("aria-label", "Play reply");
    btn.title = "Play reply";
  }

  function resetAllSpeakIcons(exceptBtn) {
    messages.querySelectorAll(".chat-speak").forEach((btn) => {
      if (btn !== exceptBtn) {
        setSpeakIcon(btn, "play");
      }
    });
  }

  function stopPlayback() {
    if (playbackAudio) {
      playbackAudio.pause();
      playbackAudio = null;
    }
    if (activeSpeakBtn) {
      setSpeakIcon(activeSpeakBtn, "play");
      activeSpeakBtn = null;
    }
  }

  function makeSpeakButton() {
    const speakBtn = document.createElement("button");
    speakBtn.type = "button";
    speakBtn.className = "chat-speak";
    setSpeakIcon(speakBtn, "play");
    return speakBtn;
  }

  function appendBubble(role, text) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${role}`;

    const body = document.createElement("span");
    body.className = "chat-bubble-text";
    body.textContent = text;
    bubble.appendChild(body);

    if (role === "bot") {
      bubble.appendChild(makeSpeakButton());
    }

    messages.appendChild(bubble);
    messages.scrollTop = messages.scrollHeight;
    return body;
  }

  function clearBubbles() {
    stopPlayback();
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
    if (micBtn && !recording) {
      micBtn.disabled = next;
    }
    chipButtons.forEach((btn) => {
      btn.disabled = next;
    });
  }

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
    const faqLabels = new Set(
      chipButtons
        .map((btn) => btn.getAttribute("data-faq-label"))
        .filter(Boolean)
    );
    return rows.some(
      (row) =>
        row &&
        row.role === "user" &&
        typeof row.text === "string" &&
        row.text.trim() &&
        !faqLabels.has(row.text.trim())
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
      if (historyHasNaturalLanguage(rows)) {
        hideFaqChips();
      } else {
        showFaqChips();
      }
    } catch (err) {
      console.error("chat history failed", err);
    }
  }

  async function resetChat() {
    historyLoaded = true;
    clearBubbles();
    showFaqChips();
    try {
      await fetch("/api/chat/reset", {
        method: "POST",
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
    } catch (err) {
      console.error("chat reset failed", err);
    }
  }

  toggle.addEventListener("click", () => {
    setOpen(panel.hidden);
  });

  closeBtn.addEventListener("click", () => {
    setOpen(false);
  });

  async function sendChat(text, options) {
    const opts = options || {};
    const displayText =
      typeof opts.displayLabel === "string" && opts.displayLabel.trim()
        ? opts.displayLabel.trim()
        : text;
    const keepChips = Boolean(opts.keepChips);

    appendBubble("user", displayText);
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
      if (!keepChips) {
        hideFaqChips();
      }
      input.focus();
      messages.scrollTop = messages.scrollHeight;
    }
  }

  async function fetchSpeakUrl(text) {
    if (audioUrlCache.has(text)) {
      return audioUrlCache.get(text);
    }
    const response = await fetch("/api/voice/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "audio/wav" },
      credentials: "same-origin",
      body: JSON.stringify({ text }),
    });
    if (!response.ok) {
      let detail = "Could not play reply.";
      try {
        const payload = await response.json();
        if (payload && typeof payload.detail === "string") {
          detail = payload.detail;
        }
      } catch {
        /* keep default */
      }
      throw new Error(detail);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    audioUrlCache.set(text, url);
    return url;
  }

  async function toggleSpeak(btn, text) {
    const cleaned = (text || "").trim();
    if (!cleaned || cleaned === "Thinking…") {
      return;
    }

    if (activeSpeakBtn === btn && playbackAudio) {
      if (!playbackAudio.paused) {
        playbackAudio.pause();
        setSpeakIcon(btn, "play");
        setVoiceStatus("");
        return;
      }
      try {
        await playbackAudio.play();
        setSpeakIcon(btn, "pause");
        setVoiceStatus("Speaking…");
      } catch {
        setVoiceStatus("Could not play audio.");
        setSpeakIcon(btn, "play");
      }
      return;
    }

    stopPlayback();
    resetAllSpeakIcons(btn);
    setSpeakIcon(btn, "pause");
    activeSpeakBtn = btn;
    setVoiceStatus("Speaking…");

    try {
      const url = await fetchSpeakUrl(cleaned);
      const audio = new Audio(url);
      playbackAudio = audio;
      audio.onended = () => {
        if (playbackAudio === audio) {
          playbackAudio = null;
        }
        if (activeSpeakBtn === btn) {
          setSpeakIcon(btn, "play");
          activeSpeakBtn = null;
        }
        setVoiceStatus("");
      };
      audio.onerror = () => {
        setVoiceStatus("Could not play audio.");
        if (activeSpeakBtn === btn) {
          setSpeakIcon(btn, "play");
          activeSpeakBtn = null;
        }
        playbackAudio = null;
      };
      await audio.play();
    } catch (err) {
      setVoiceStatus(err && err.message ? err.message : "Could not reach the voice service.");
      setSpeakIcon(btn, "play");
      activeSpeakBtn = null;
      playbackAudio = null;
    }
  }

  messages.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const btn = target.closest(".chat-speak");
    if (!btn || !messages.contains(btn)) {
      return;
    }
    const bubble = btn.closest(".chat-bubble");
    const textNode = bubble ? bubble.querySelector(".chat-bubble-text") : null;
    const text = textNode ? textNode.textContent : "";
    toggleSpeak(btn, text);
  });

  async function transcribeBlob(blob) {
    const formData = new FormData();
    const type = blob.type || "audio/webm";
    const ext = type.includes("ogg") ? "ogg" : type.includes("mp4") ? "mp4" : "webm";
    formData.append("file", blob, `recording.${ext}`);
    setVoiceStatus("Transcribing…");
    const response = await fetch("/api/voice/transcribe", {
      method: "POST",
      credentials: "same-origin",
      body: formData,
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
          : "Transcription failed.";
      throw new Error(detail);
    }
    const text = payload && typeof payload.text === "string" ? payload.text.trim() : "";
    if (!text) {
      throw new Error("Could not hear speech. Try again or type your message.");
    }
    return text;
  }

  function setRecording(active) {
    recording = active;
    if (!micBtn) {
      return;
    }
    micBtn.setAttribute("aria-pressed", active ? "true" : "false");
    micBtn.disabled = busy && !active;
    setMicIcon(active);
  }

  async function stopRecording() {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
      setRecording(false);
      return;
    }
    await new Promise((resolve) => {
      mediaRecorder.addEventListener("stop", resolve, { once: true });
      mediaRecorder.stop();
    });
    setRecording(false);
    const blob = new Blob(recordChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    recordChunks = [];
    mediaRecorder.stream.getTracks().forEach((track) => track.stop());
    mediaRecorder = null;
    if (!blob.size) {
      setVoiceStatus("No audio captured.");
      return;
    }
    try {
      const text = await transcribeBlob(blob);
      input.value = text;
      resizeInput();
      input.focus();
      setVoiceStatus("Transcript ready — edit if needed, then Send.");
    } catch (err) {
      setVoiceStatus(err && err.message ? err.message : "Transcription failed.");
    }
  }

  async function startRecording() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      setVoiceStatus("Microphone not available in this browser. Type instead.");
      return;
    }
    if (busy) {
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordChunks = [];
      const options = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? { mimeType: "audio/webm;codecs=opus" }
        : undefined;
      mediaRecorder = new MediaRecorder(stream, options);
      mediaRecorder.addEventListener("dataavailable", (event) => {
        if (event.data && event.data.size) {
          recordChunks.push(event.data);
        }
      });
      mediaRecorder.start();
      setRecording(true);
      setVoiceStatus("Listening… click stop to finish.");
    } catch {
      setVoiceStatus("Microphone permission denied. Type instead.");
    }
  }

  if (micBtn) {
    setMicIcon(false);
    micBtn.addEventListener("click", async () => {
      if (recording) {
        await stopRecording();
        return;
      }
      await startRecording();
    });
  }

  chipButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (btn.disabled || busy) {
        return;
      }
      const token = btn.getAttribute("data-faq-token");
      const label = btn.getAttribute("data-faq-label") || token;
      if (!token) {
        return;
      }
      sendChat(token, { displayLabel: label, keepChips: true });
    });
  });

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 104)}px`;
  }

  input.addEventListener("input", resizeInput);

  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing) {
      return;
    }
    event.preventDefault();
    if (busy || !input.value.trim()) {
      return;
    }
    form.requestSubmit();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text || busy) {
      return;
    }
    input.value = "";
    resizeInput();
    setVoiceStatus("");
    await sendChat(text);
  });

  // sessionStorage lasts for this tab only (survives in-tab navigation / refresh;
  // cleared when the tab is closed). Cookie session alone would leak across tabs.
  async function bootstrapChat() {
    const sameTab = sessionStorage.getItem(TAB_CHAT_KEY) === "1";
    if (!sameTab) {
      sessionStorage.setItem(TAB_CHAT_KEY, "1");
      await resetChat();
      return;
    }
    await loadHistory();
  }

  bootstrapChat();
})();
