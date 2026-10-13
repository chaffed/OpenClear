// The control panel (templates/settings/panel.html; UX_PLAN.md U11) and the setting pages
// it opens (templates/settings/section.html).
//
// - The search box filters the tiles. Its text is kept when you come back to the panel.
// - A tile marked data-modal opens its setting in a <dialog>, fetched from the tile's own
//   URL with ?partial=1, and that URL goes in the address bar, so Back closes it and the
//   link can be shared. Without JavaScript the same URL is a plain page.
// - Saving sends the form in the background: on success the dialog closes, the tiles'
//   states refresh and a message shows; on an error the form comes back with it.
// - Closing with unsaved changes (the close button, Escape, the backdrop, or Back) asks first.
// - Coming back from a page or a dialog puts focus on the tile that opened it.

(function () {
  const SEARCH_KEY = "openclear.panel.search";
  const TILE_KEY = "openclear.panel.tile";

  function store(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (e) { /* storage can be unavailable; the panel still works */ }
  }

  function recall(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function navigationType() {
    const entries = performance.getEntriesByType ? performance.getEntriesByType("navigation") : [];
    return entries.length ? entries[0].type : "navigate";
  }

  function showToast(message) {
    const region = document.getElementById("toast-region");
    if (!region || !message) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.appendChild(close);
    region.appendChild(toast);
    setTimeout(() => toast.remove(), 8000);
  }

  function confirmDiscard() {
    const message = "You have changes that aren't saved. Close without saving?";
    if (typeof askToConfirm === "function") return askToConfirm(message, { title: "Discard changes?", yes: "Discard", danger: true });
    return Promise.resolve(window.confirm(message));
  }

  // The security event form shows only the fields for the chosen kind of destination.
  function wireSiemForm(root) {
    root.querySelectorAll("[data-siem-form]").forEach((form) => {
      const select = form.querySelector("[data-siem-kind]");
      if (!select) return;
      const apply = () => {
        form.querySelectorAll("[data-siem-for]").forEach((el) => {
          const shown = el.dataset.siemFor.split(" ").includes(select.value);
          el.hidden = !shown;
          el.querySelectorAll("input, select, textarea").forEach((input) => { input.disabled = !shown; });
        });
      };
      select.addEventListener("change", apply);
      apply();
    });
  }

  function wireEditors(root) {
    if (typeof initMarkdownEditor === "function") root.querySelectorAll("[data-markdown-editor]").forEach(initMarkdownEditor);
  }

  // --- A setting shown as its own page --------------------------------------------------------

  function initSectionPage(section) {
    wireSiemForm(section);
    let dirty = false;
    section.addEventListener("input", () => { dirty = true; });
    section.addEventListener("change", () => { dirty = true; });
    section.addEventListener("submit", () => { dirty = false; });
    window.addEventListener("beforeunload", (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  // --- The panel ------------------------------------------------------------------------------

  function initPanel(panelSearch) {
    const tiles = Array.from(document.querySelectorAll("[data-panel-tile]"));
    const sections = Array.from(document.querySelectorAll("[data-panel-section]"));
    const empty = document.querySelector("[data-panel-empty]");
    const dialog = document.getElementById("settings-dialog");
    const body = dialog ? dialog.querySelector("[data-dialog-body]") : null;
    const title = dialog ? dialog.querySelector("#settings-dialog-title") : null;
    const params = new URLSearchParams(window.location.search);
    const returning = params.has("from") || navigationType() === "back_forward";

    // Search ------------------------------------------------------------------------------
    function filter() {
      const words = panelSearch.value.toLowerCase().split(/\s+/).filter(Boolean);
      let shown = 0;
      tiles.forEach((li) => {
        const match = words.every((w) => li.dataset.search.includes(w));
        li.hidden = !match;
        if (match) shown += 1;
      });
      sections.forEach((section) => {
        section.hidden = !section.querySelector("[data-panel-tile]:not([hidden])");
      });
      if (empty) empty.hidden = shown > 0;
      store(SEARCH_KEY, panelSearch.value || null);
    }
    if (returning) panelSearch.value = recall(SEARCH_KEY) || panelSearch.value;
    else store(SEARCH_KEY, null);
    panelSearch.addEventListener("input", filter);
    filter();

    function tileLink(key) {
      return document.querySelector(`.tile[data-key="${CSS.escape(key)}"]`);
    }

    // Focus the tile you came back from ----------------------------------------------------
    const from = params.get("from") || (returning ? recall(TILE_KEY) : null);
    if (from && tileLink(from)) {
      const link = tileLink(from);
      link.closest("[data-panel-tile]").hidden = false;
      link.focus();
    }
    if (params.has("from")) {
      params.delete("from");
      const query = params.toString();
      history.replaceState(history.state, "", window.location.pathname + (query ? `?${query}` : ""));
    }

    // Tile states that take a moment to work out (the controls) ----------------------------
    function loadLazyStates() {
      document.querySelectorAll("[data-state-url]").forEach((el) => {
        fetch(el.dataset.stateUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
          .then((r) => (r.ok ? r.json() : null))
          .then((data) => {
            if (!data) { el.textContent = ""; return; }
            el.textContent = data.state;
            el.className = "tile-state" + (data.tone ? ` tone-${data.tone}` : "");
          })
          .catch(() => { el.textContent = ""; });
      });
    }
    loadLazyStates();

    function refreshStates() {
      return fetch("/ui/settings/tile-states", { credentials: "same-origin", headers: { Accept: "application/json" } })
        .then((r) => (r.ok ? r.json() : {}))
        .then((states) => {
          Object.entries(states).forEach(([key, value]) => {
            const link = tileLink(key);
            const el = link && link.querySelector("[data-tile-state]");
            if (!el || el.dataset.stateUrl) return;
            el.textContent = value.state;
            el.className = "tile-state" + (value.tone ? ` tone-${value.tone}` : "");
            const li = link.closest("[data-panel-tile]");
            li.dataset.search = li.dataset.search.replace(/\s+$/, "") + " " + value.state.toLowerCase();
          });
        })
        .catch(() => {});
    }

    document.querySelectorAll(".tile").forEach((link) => {
      link.addEventListener("click", () => store(TILE_KEY, link.dataset.key));
    });

    if (!dialog || typeof dialog.showModal !== "function") return;

    // Modals ------------------------------------------------------------------------------
    let current = null; // {key, href}
    let dirty = false;

    function wire() {
      wireSiemForm(body);
      wireEditors(body);
      body.querySelectorAll("form").forEach((form) => {
        form.addEventListener("input", () => { dirty = true; });
        form.addEventListener("change", () => { dirty = true; });
        form.addEventListener("submit", (event) => submitInBackground(event, form));
      });
    }

    function show(section) {
      body.replaceChildren(document.importNode(section, true));
      title.textContent = section.dataset.title || (current && tileLink(current.key)?.dataset.title) || "Settings";
      wire();
      const error = body.querySelector("#settings-error");
      const first = error ? null : body.querySelector("input:not([type=hidden]):not([disabled]), select:not([disabled]), textarea");
      if (error) {
        error.setAttribute("tabindex", "-1");
        error.focus();
      } else if (first && !window.OpenClear.breakpoint("phone").matches) {
        first.focus();
      } else {
        dialog.querySelector("[data-dialog-close]").focus();
      }
    }

    function openTile(key, href, push) {
      current = { key, href };
      dirty = false;
      title.textContent = tileLink(key)?.dataset.title || "Settings";
      body.replaceChildren();
      body.setAttribute("aria-busy", "true");
      if (!dialog.open) dialog.showModal();
      if (push) history.pushState({ modal: key }, "", href);
      const url = new URL(href, window.location.href);
      url.searchParams.set("partial", "1");
      fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
        .then((r) => {
          if (r.redirected || !r.ok) throw new Error("not available");
          return r.text();
        })
        .then((html) => {
          const section = new DOMParser().parseFromString(html, "text/html").getElementById("settings-section");
          if (!section) throw new Error("not a setting");
          body.removeAttribute("aria-busy");
          show(section);
        })
        .catch(() => { window.location.href = href; });
    }

    function closeNow() {
      if (dialog.open) dialog.close();
      body.replaceChildren();
      const key = current && current.key;
      current = null;
      dirty = false;
      if (key && tileLink(key)) tileLink(key).focus();
    }

    // Closing goes back in history, so the address bar and Back stay in step; popstate closes it.
    function requestClose() {
      if (!current) { closeNow(); return; }
      const go = () => {
        dirty = false;
        if (history.state && history.state.modal) history.back();
        else closeNow();
      };
      if (dirty) confirmDiscard().then((ok) => { if (ok) go(); });
      else go();
    }

    function submitInBackground(event, form) {
      if (event.defaultPrevented) return;
      event.preventDefault();
      const submitter = event.submitter;
      const send = () => {
        let data;
        try {
          data = new FormData(form, submitter || undefined);
        } catch (e) {
          data = new FormData(form);
          if (submitter && submitter.name) data.append(submitter.name, submitter.value);
        }
        form.setAttribute("aria-busy", "true");
        form.querySelectorAll("button").forEach((b) => { b.disabled = true; });
        if (submitter && submitter.tagName === "BUTTON") submitter.textContent = submitter.dataset.busyLabel || "Saving…";
        // Saved: the server answers {"saved": true, "message": ...}. Not saved: the setting
        // again, with the error. Anything else (signed out, say): follow it as a page.
        fetch(form.action, { method: "POST", body: data, credentials: "same-origin", headers: { "X-Requested-With": "fetch" } })
          .then((r) => r.text().then((text) => ({ r, text })))
          .then(({ r, text }) => {
            if ((r.headers.get("content-type") || "").includes("application/json")) {
              let reply = {};
              try { reply = JSON.parse(text); } catch (e) { /* treated as not saved below */ }
              if (reply.saved) {
                dirty = false;
                requestClose();
                refreshStates();
                showToast(reply.message || "Saved.");
                return;
              }
            }
            const section = new DOMParser().parseFromString(text, "text/html").getElementById("settings-section");
            if (section) show(section);
            else window.location.href = r.redirected ? r.url : (current ? current.href : "/ui/settings"); // signed out, say
          })
          .catch(() => {
            form.removeAttribute("aria-busy");
            form.querySelectorAll("button").forEach((b) => { b.disabled = false; });
            showToast("Couldn't save. Check your connection and try again.");
          });
      };
      const confirmMessage = submitter && submitter.dataset.confirm;
      if (confirmMessage && typeof askToConfirm === "function") {
        askToConfirm(confirmMessage, { yes: submitter.dataset.confirmYes, danger: submitter.hasAttribute("data-confirm-danger") })
          .then((ok) => { if (ok) send(); });
      } else {
        send();
      }
    }

    document.querySelectorAll(".tile[data-modal]").forEach((link) => {
      link.addEventListener("click", (event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
        event.preventDefault();
        openTile(link.dataset.key, link.getAttribute("href"), true);
      });
    });

    dialog.querySelector("[data-dialog-close]").addEventListener("click", requestClose);
    dialog.addEventListener("cancel", (event) => { // Escape
      event.preventDefault();
      requestClose();
    });
    dialog.addEventListener("click", (event) => { // the backdrop
      if (event.target === dialog) requestClose();
    });

    window.addEventListener("popstate", (event) => {
      const state = event.state || {};
      if (current && state.modal !== current.key) {
        if (dirty) {
          // Back with unsaved changes: stay put until they say so.
          const keep = current;
          history.pushState({ modal: keep.key }, "", keep.href);
          confirmDiscard().then((ok) => {
            if (ok) { dirty = false; history.back(); }
          });
          return;
        }
        closeNow();
      } else if (!current && state.modal) {
        const link = tileLink(state.modal);
        if (link) openTile(state.modal, link.getAttribute("href"), false);
      }
    });

    window.addEventListener("beforeunload", (event) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    const panelSearch = document.querySelector("[data-panel-search]");
    if (panelSearch) initPanel(panelSearch);
    const section = document.getElementById("settings-section");
    if (section && !panelSearch) initSectionPage(section);
  });
})();
