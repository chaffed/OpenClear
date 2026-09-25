// Keeps an active user signed in, and warns an idle one before the session times out.
//
// base.html publishes two times (Unix seconds) for the current session:
//   openclear-access-expires  -- the idle timeout: the access token's expiry. Renewing it
//                             (POST /ui/auth/refresh) is only allowed before it passes;
//                             the server enforces that (web/routers/session.py).
//   openclear-session-expires -- the maximum session length. Renewal never goes past it.
//
// Activity (typing, clicking, scrolling) renews quietly in the background, throttled.
// With no activity, a dialog appears WARN_MS before the idle timeout with a countdown and
// "Stay signed in" / "Sign out now". Near the maximum length, which can't be extended,
// the dialog instead says to save work. Tabs share cookies, so they share the new times
// through localStorage and one active tab keeps the others from warning.
(() => {
  const accessMeta = document.querySelector('meta[name="openclear-access-expires"]');
  const dialog = document.getElementById("session-dialog");
  if (!accessMeta || !dialog) return;

  const WARN_MS = 5 * 60 * 1000;
  const STORAGE_KEY = "openclear-session-times";
  const sessionMeta = document.querySelector('meta[name="openclear-session-expires"]');

  let accessExpires = Number(accessMeta.content) * 1000;
  let sessionExpires = sessionMeta ? Number(sessionMeta.content) * 1000 : Infinity;
  let lastRefresh = Date.now();
  let refreshing = false;

  const titleEl = dialog.querySelector("[data-session-title]");
  const messageEl = dialog.querySelector("[data-session-message]");
  const stayButton = dialog.querySelector("[data-session-stay]");
  const signOutButton = dialog.querySelector("[data-session-signout]");

  const csrfToken = () => {
    const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const formatRemaining = (ms) => {
    const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = String(totalSeconds % 60).padStart(2, "0");
    return `${minutes}:${seconds}`;
  };

  const adopt = (access, session) => {
    if (access <= accessExpires) return;
    accessExpires = access;
    sessionExpires = session;
    lastRefresh = Date.now();
    if (dialog.open) dialog.close();
  };

  const refresh = async () => {
    if (refreshing) return false;
    refreshing = true;
    try {
      const response = await fetch("/ui/auth/refresh", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      if (!response.ok) return false;
      const body = await response.json();
      adopt(body.access_expires_at * 1000, body.session_expires_at * 1000);
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify({ access: accessExpires, session: sessionExpires }));
      } catch (e) {
        // Storage unavailable (private mode, blocked): other tabs just won't hear about it.
      }
      return true;
    } catch (e) {
      return false;
    } finally {
      refreshing = false;
    }
  };

  const signOut = () => {
    const form = document.querySelector('form[action="/ui/logout"]');
    if (form) form.submit();
    else window.location.href = "/ui/login";
  };

  const returnToSignIn = () => {
    // Past the idle timeout (plus the server's small grace period) this lands on the
    // sign-in page, which then returns the user to this page.
    const here = window.location.pathname + window.location.search;
    window.location.href = `/ui/auth/resume?next=${encodeURIComponent(here)}`;
  };

  // Renew on activity, at most once per third of the idle timeout (and never more than
  // once a minute), so the timeout effectively counts from the user's last activity.
  const onActivity = () => {
    if (dialog.open) return; // an open warning needs an explicit answer
    const lifetime = Math.max(0, accessExpires - lastRefresh);
    const throttle = Math.max(60 * 1000, lifetime / 3);
    if (Date.now() - lastRefresh >= throttle && accessExpires < sessionExpires) refresh();
  };
  ["keydown", "mousedown", "touchstart", "scroll", "input"].forEach((type) =>
    window.addEventListener(type, onActivity, { passive: true, capture: true })
  );

  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY || !event.newValue) return;
    try {
      const times = JSON.parse(event.newValue);
      adopt(times.access, times.session);
    } catch (e) {
      // Ignore malformed values.
    }
  });

  stayButton.addEventListener("click", async () => {
    if (!(await refresh())) returnToSignIn();
  });
  signOutButton.addEventListener("click", signOut);
  dialog.addEventListener("cancel", (event) => event.preventDefault()); // Esc mustn't silently dismiss it

  const showWarning = (endsAtMaximum) => {
    if (endsAtMaximum) {
      titleEl.textContent = "Your session is ending";
      stayButton.hidden = true;
      signOutButton.textContent = "Sign out now";
    } else {
      titleEl.textContent = "Are you still there?";
      stayButton.hidden = false;
    }
    if (!dialog.open) {
      dialog.showModal();
      (endsAtMaximum ? signOutButton : stayButton).focus();
    }
  };

  const countdownEl = (text) => {
    const strong = document.createElement("strong");
    strong.dataset.sessionCountdown = "";
    strong.textContent = text;
    return strong;
  };

  const tick = () => {
    const now = Date.now();
    const endsAt = Math.min(accessExpires, sessionExpires);
    const remaining = endsAt - now;
    if (remaining <= 0) {
      returnToSignIn();
      return;
    }
    if (remaining <= WARN_MS) {
      const endsAtMaximum = sessionExpires <= accessExpires;
      showWarning(endsAtMaximum);
      const countdown = formatRemaining(remaining);
      messageEl.innerHTML = "";
      if (endsAtMaximum) {
        messageEl.append(
          "You've reached the maximum session length and will be signed out in ",
          countdownEl(countdown),
          ". Save your work, then sign in again to continue."
        );
      } else {
        messageEl.append(
          "You'll be signed out in ",
          countdownEl(countdown),
          " because you haven't been active."
        );
      }
    }
  };
  window.setInterval(tick, 1000);
})();
