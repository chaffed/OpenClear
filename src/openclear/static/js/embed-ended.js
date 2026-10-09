// The page a frame lands on when its embedded session ends (web/routers/embed.py). It tells
// the online banking page, which can start a new session. No data is sent, so any parent
// may hear it.
if (window.parent !== window) {
  window.parent.postMessage({ source: "openclear", type: "session-ended" }, "*");
}
