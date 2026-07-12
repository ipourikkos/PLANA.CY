* {
  box-sizing: border-box;
}

:root {
  --bg: #f3f4f2;
  --panel: #ffffff;
  --text: #17211b;
  --muted: #68726c;
  --line: #dfe4e0;
  --accent: #173f2b;
  --accent-soft: #e9f0eb;
  --user: #173f2b;
  --user-text: #ffffff;
  --danger: #9b2c2c;
}

body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--text);
}

button, textarea {
  font: inherit;
}

.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 82px;
  padding: 16px 28px;
  border-bottom: 1px solid var(--line);
  background: rgba(255,255,255,0.92);
  backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: sticky;
  top: 0;
  z-index: 10;
}

.eyebrow {
  font-size: 11px;
  letter-spacing: 0.14em;
  font-weight: 700;
  color: var(--muted);
}

h1 {
  margin: 4px 0 0;
  font-size: 22px;
  font-weight: 700;
}

.status-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--muted);
}

.status-dot {
  width: 9px;
  height: 9px;
  border-radius: 999px;
  background: #a8afa9;
}

.status-dot.online {
  background: #2f8f5b;
}

.status-dot.offline {
  background: var(--danger);
}

.chat-layout {
  width: min(980px, 100%);
  margin: 0 auto;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.messages {
  padding: 42px 22px 180px;
}

.welcome-card {
  max-width: 720px;
  margin: 40px auto 0;
  text-align: center;
}

.welcome-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto 18px;
  border-radius: 14px;
  display: grid;
  place-items: center;
  background: var(--accent);
  color: white;
  font-size: 24px;
}

.welcome-card h2 {
  margin: 0 0 10px;
  font-size: 30px;
}

.welcome-card p {
  margin: 0 auto;
  max-width: 620px;
  color: var(--muted);
  line-height: 1.6;
}

.example-grid {
  margin-top: 28px;
  display: grid;
  gap: 10px;
}

.example-btn {
  border: 1px solid var(--line);
  background: var(--panel);
  color: var(--text);
  border-radius: 14px;
  padding: 14px 16px;
  text-align: left;
  cursor: pointer;
  transition: 0.15s ease;
}

.example-btn:hover {
  border-color: #b9c7bd;
  transform: translateY(-1px);
}

.message-row {
  display: flex;
  margin: 22px 0;
}

.user-row {
  justify-content: flex-end;
}

.user-message {
  max-width: 72%;
  background: var(--user);
  color: var(--user-text);
  border-radius: 18px 18px 4px 18px;
  padding: 13px 16px;
  line-height: 1.5;
  white-space: pre-wrap;
}

.assistant-row {
  justify-content: flex-start;
}

.assistant-card {
  width: min(860px, 100%);
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 18px;
  padding: 22px;
  box-shadow: 0 8px 28px rgba(21, 44, 30, 0.05);
}

.assistant-label {
  color: var(--accent);
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.12em;
  margin-bottom: 14px;
}

.answer {
  line-height: 1.7;
  font-size: 15.5px;
}

.answer p {
  margin: 0 0 14px;
}

.answer ul {
  margin: 8px 0 14px;
  padding-left: 22px;
}

.answer li {
  margin: 6px 0;
}

.answer strong {
  color: #0d2f1f;
}

.sources-panel {
  margin-top: 18px;
  border-top: 1px solid var(--line);
  padding-top: 14px;
}

.sources-panel summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 14px;
  font-weight: 600;
}

.sources-list {
  display: grid;
  gap: 10px;
  margin-top: 12px;
}

.source-card {
  background: var(--accent-soft);
  border: 1px solid #d7e2da;
  border-radius: 12px;
  padding: 12px 14px;
}

.source-title {
  font-weight: 700;
  line-height: 1.4;
}

.source-meta {
  margin-top: 5px;
  font-size: 12px;
  color: var(--muted);
}

.loading-card {
  color: var(--muted);
}

.error-card {
  border-color: #e2bcbc;
  color: var(--danger);
}

.composer-wrap {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 18px 20px 14px;
  background: linear-gradient(to top, var(--bg) 72%, rgba(243,244,242,0));
}

.composer {
  width: min(936px, calc(100% - 44px));
  margin: 0 auto;
  background: var(--panel);
  border: 1px solid #cfd8d1;
  border-radius: 18px;
  padding: 10px 10px 10px 16px;
  display: flex;
  gap: 10px;
  align-items: flex-end;
  box-shadow: 0 12px 34px rgba(20, 42, 28, 0.10);
}

textarea {
  flex: 1;
  border: 0;
  resize: none;
  outline: none;
  min-height: 42px;
  max-height: 180px;
  padding: 10px 2px;
  background: transparent;
  color: var(--text);
  line-height: 1.45;
}

#sendButton {
  border: 0;
  background: var(--accent);
  color: white;
  border-radius: 12px;
  padding: 11px 18px;
  cursor: pointer;
  font-weight: 700;
}

#sendButton:disabled {
  opacity: 0.55;
  cursor: wait;
}

.disclaimer {
  width: min(936px, calc(100% - 44px));
  margin: 8px auto 0;
  text-align: center;
  font-size: 11px;
  color: var(--muted);
}

@media (max-width: 700px) {
  .topbar {
    padding: 14px 16px;
  }

  .status-text {
    display: none;
  }

  .messages {
    padding-left: 14px;
    padding-right: 14px;
  }

  .user-message {
    max-width: 88%;
  }

  .assistant-card {
    padding: 17px;
  }

  .composer {
    width: calc(100% - 24px);
  }

  .disclaimer {
    width: calc(100% - 24px);
  }
}
