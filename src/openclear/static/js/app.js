document.addEventListener("DOMContentLoaded", () => {
  // Wide tables scroll sideways inside their own card on narrow screens, instead of
  // widening the whole page (which made every list page scroll horizontally on a phone).
  // Done here once for every table rather than in each of the templates.
  document.querySelectorAll("table").forEach((table) => {
    if (table.parentElement && table.parentElement.classList.contains("table-scroll")) return;
    const wrapper = document.createElement("div");
    wrapper.className = "table-scroll";
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });

  // A table wider than its box scrolls inside it; keyboard users then need to reach the box
  // to scroll it, so it's focusable and named, but only while it actually overflows.
  const markScrollable = () => document.querySelectorAll(".table-scroll").forEach((box) => {
    if (box.scrollWidth > box.clientWidth + 1) {
      box.setAttribute("tabindex", "0");
      box.setAttribute("role", "region");
      box.setAttribute("aria-label", "Table, scrolls sideways");
    } else {
      box.removeAttribute("tabindex");
      box.removeAttribute("role");
      box.removeAttribute("aria-label");
    }
  });
  markScrollable();
  window.addEventListener("resize", markScrollable);

  initConfirmations();
  initBusyForms();
  initDropzones();
  initLazyStates();
  initToasts();
  initPolling();

  // A link with data-history-back returns to the previous page (e.g. error.html's "Go
  // back", so a rejected form's typed values are still there) — wired up here rather
  // than as a javascript: href, which the strict script-src CSP blocks. Its real href is
  // the no-JS / no-history fallback.
  initDecisionKeys();
  initBulkDecisions();

  // <time datetime="..." data-local-time> shows the reviewer's own local time instead of
  // the server's UTC fallback text; data-relative-time adds "in 2h 13m" / "overdue".
  document.querySelectorAll("time[data-local-time]").forEach((el) => {
    const when = new Date(el.getAttribute("datetime"));
    if (Number.isNaN(when.getTime())) return;
    let text = when.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    if (el.hasAttribute("data-relative-time")) {
      const minutes = Math.round((when.getTime() - Date.now()) / 60000);
      if (minutes <= 0) {
        text += " (overdue)";
      } else {
        const hours = Math.floor(minutes / 60);
        text += ` (in ${hours ? `${hours}h ` : ""}${minutes % 60}m)`;
      }
    }
    el.textContent = text;
  });

  // A button with data-copy-target="<element id>" copies that element's text (e.g. the
  // one-time temporary password on users/reset_password_result.html) and briefly
  // confirms. Clipboard access can be refused (permissions, non-HTTPS origin), in which
  // case the text is still selectable by hand.
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target || !navigator.clipboard) return;
      navigator.clipboard.writeText(target.textContent.trim()).then(() => {
        const original = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = original; }, 2000);
      }).catch(() => {});
    });
  });

  document.querySelectorAll("[data-history-back]").forEach((el) => {
    el.addEventListener("click", (event) => {
      if (window.history.length > 1) {
        event.preventDefault();
        window.history.back();
      }
    });
  });

  // A <select>/<input> with data-auto-submit submits its enclosing form on change --
  // used instead of an inline onchange="..." attribute so the app can run a strict,
  // nonce-based script-src Content-Security-Policy (see web/security_headers.py) with no
  // 'unsafe-inline'. data-auto-submit="request" uses requestSubmit() instead of submit()
  // where a route relies on the submit button/native validation running (submit() skips
  // both); plain data-auto-submit uses the plain, no-validation submit().
  document.querySelectorAll("[data-auto-submit]").forEach((el) => {
    el.addEventListener("change", () => {
      if (el.dataset.autoSubmit === "request") {
        el.form.requestSubmit();
      } else {
        el.form.submit();
      }
    });
  });

  initSortableTables();
  initHelpDialog();
  initNavToggle();
  initBottomSheets();
  initBannerCarousel();
});

// templates/partials/_banner.html renders one .banner-slide per message that's actually
// set (tenant's own, and/or the logged-in user's own customer's) -- 0 or 1 slides need no
// rotation at all (a no-op here; CSS shows the lone slide, if any, on its own). With 2,
// auto-advance between them (tenant's shows first, per _banner.html's own slide order),
// looping indefinitely; the dots are clickable and reset the timer on manual pick rather
// than fighting it. Uses inline style.display (not a CSS "hide unless .active" rule) so a
// single-slide carousel still renders correctly with zero JS/CSS coordination needed.
function initBannerCarousel() {
  document.querySelectorAll("[data-banner-carousel]").forEach((carousel) => {
    const slides = Array.from(carousel.querySelectorAll(".banner-slide"));
    const dots = Array.from(carousel.querySelectorAll(".banner-dot"));
    if (slides.length < 2) return;

    let current = 0;
    let timer = null;

    const show = (index) => {
      current = index;
      slides.forEach((slide, i) => {
        slide.style.display = i === index ? "" : "none";
      });
      dots.forEach((dot, i) => dot.classList.toggle("active", i === index));
    };

    const startAutoAdvance = () => {
      if (timer) clearInterval(timer);
      timer = setInterval(() => show((current + 1) % slides.length), 8000);
    };

    dots.forEach((dot, i) => {
      dot.addEventListener("click", () => {
        show(i);
        startAutoAdvance();
      });
    });

    show(0);
    startAutoAdvance();
  });
}

// The navigation drawer below desktop width (base.html's #nav-toggle / #sidenav-panel, app.css's
// drawer rules; MOBILE_PLAN.md R1). Open, the page behind it is inert, so focus stays in the
// drawer; Escape, the close button or a tap on the backdrop closes it, and focus goes back to
// the menu button. At desktop width CSS shows the panel as the sidebar whatever its class.
// No "close on navigate": every link is a full page load.
function initNavToggle() {
  const toggle = document.getElementById("nav-toggle");
  const panel = document.getElementById("sidenav-panel");
  if (!toggle || !panel) return;
  const page = document.getElementById("main-content");
  let backdrop = null;

  const close = (returnFocus = true) => {
    if (!panel.classList.contains("open")) return;
    panel.classList.remove("open");
    toggle.setAttribute("aria-expanded", "false");
    document.body.classList.remove("nav-open");
    if (page) page.inert = false;
    if (backdrop) backdrop.remove();
    backdrop = null;
    if (returnFocus) toggle.focus();
  };
  const open = () => {
    panel.classList.add("open");
    toggle.setAttribute("aria-expanded", "true");
    document.body.classList.add("nav-open");
    if (page) page.inert = true;
    backdrop = document.createElement("div");
    backdrop.className = "nav-backdrop";
    backdrop.addEventListener("click", () => close());
    document.body.appendChild(backdrop);
    const first = panel.querySelector("a[href], input:not([type=hidden])");
    if (first) first.focus();
  };

  toggle.addEventListener("click", () => (panel.classList.contains("open") ? close() : open()));
  panel.querySelectorAll("[data-nav-close]").forEach((button) => button.addEventListener("click", () => close()));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panel.classList.contains("open")) close();
  });
  window.OpenClear.breakpoint("tablet").addEventListener("change", (event) => {
    if (!event.matches) close(false);
  });
}

// Bottom sheets (templates/_macros/bottom_sheet.html; MOBILE_PLAN.md §1). Below the content's
// breakpoint it moves into a <dialog> with a title and a Close button, opened by its trigger;
// above it, it goes back where it was (and the dialog is gone), so wider screens never see
// the sheet. A <details> as the content is opened while it's in the sheet.
function initBottomSheets() {
  document.querySelectorAll("[data-sheet]").forEach((content) => {
    const query = window.OpenClear.breakpoint(content.dataset.sheet);
    const triggers = document.querySelectorAll(`[data-sheet-open="${content.id}"]`);
    let dialog = null;
    let wasOpen = false;

    const place = () => {
      if (query.matches && !dialog) {
        dialog = document.createElement("dialog");
        dialog.className = "bottom-sheet";
        dialog.setAttribute("aria-labelledby", `${content.id}-title`);
        const head = document.createElement("div");
        head.className = "bottom-sheet-head";
        const title = document.createElement("h2");
        title.id = `${content.id}-title`;
        title.textContent = content.dataset.sheetTitle || "";
        const close = document.createElement("button");
        close.type = "button";
        close.className = "secondary bottom-sheet-close";
        close.textContent = "Close";
        close.addEventListener("click", () => dialog.close());
        head.append(title, close);
        content.parentNode.insertBefore(dialog, content);
        dialog.append(head, content);
        // A click on the dialog itself, not its content, is the backdrop.
        dialog.addEventListener("click", (event) => {
          if (event.target === dialog) dialog.close();
        });
        if (content.tagName === "DETAILS") {
          wasOpen = content.open;
          content.open = true;
        }
        triggers.forEach((trigger) => { trigger.hidden = false; });
      } else if (!query.matches && dialog) {
        if (dialog.open) dialog.close();
        dialog.parentNode.insertBefore(content, dialog);
        dialog.remove();
        dialog = null;
        if (content.tagName === "DETAILS") content.open = wasOpen;
        triggers.forEach((trigger) => { trigger.hidden = true; });
      }
    };

    triggers.forEach((trigger) => trigger.addEventListener("click", () => {
      if (dialog && !dialog.open) dialog.showModal();
    }));
    place();
    query.addEventListener("change", place);
  });
}

// Opt-in per page via base.html's help_title/help_body blocks — the button and
// <dialog> only render at all when a page defines help_body (see base.html), so this
// is a no-op on every page that doesn't.
function initHelpDialog() {
  const dialog = document.getElementById("page-help-dialog");
  const button = document.getElementById("page-help-button");
  if (!dialog || !button) return;

  button.addEventListener("click", () => dialog.showModal());
  const closeButton = dialog.querySelector(".help-close");
  if (closeButton) closeButton.addEventListener("click", () => dialog.close());

  // Native <dialog> doesn't close on backdrop click by itself — a click that lands on
  // the dialog element itself (not one of its content children) means the backdrop.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
}

// Any <table data-sortable> gets a filter box injected above it and click-to-sort
// headers — entirely client-side (no server round-trip, no build step), which fits
// this app's list sizes and its "no Node/build step" design elsewhere. Mark a header
// data-no-sort to exclude it (e.g. a trailing actions column with no meaningful order),
// or data-type="number" for columns that must sort numerically rather than as text
// (amounts, counts) — plain text columns already sort correctly with numeric-aware
// locale comparison, including ISO-formatted dates, so no separate "date" type exists.
function initSortableTables() {
  document.querySelectorAll("table[data-sortable]").forEach((table) => {
    const tbody = table.tBodies[0];
    if (!tbody) return;
    const rows = Array.from(tbody.rows);

    // On a list with more than one page, the rows here are only this page's: say so, so
    // nobody concludes an item doesn't exist because it isn't on this page. (The main
    // lists search the server instead; see web/list_search.py.)
    const pager = table.parentNode.querySelector(".pagination[data-pages]");
    const pageOnly = pager && parseInt(pager.dataset.pages, 10) > 1;
    const search = document.createElement("input");
    search.type = "search";
    search.className = "table-search";
    search.placeholder = pageOnly ? "Filter this page only..." : "Filter...";
    search.setAttribute("aria-label", pageOnly ? "Filter the rows on this page only" : "Filter table rows");
    table.parentNode.insertBefore(search, table);

    search.addEventListener("input", () => {
      const query = search.value.trim().toLowerCase();
      rows.forEach((row) => {
        row.style.display = !query || row.textContent.toLowerCase().includes(query) ? "" : "none";
      });
    });

    if (!table.tHead) return;
    const headers = Array.from(table.tHead.rows[0].cells);
    headers.forEach((th, index) => {
      if (th.hasAttribute("data-no-sort") || !th.textContent.trim()) return;
      // The header stays a column header (aria-sort belongs there); a button inside it sorts.
      th.classList.add("sortable-col");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "sort-button";
      while (th.firstChild) button.appendChild(th.firstChild);
      th.appendChild(button);

      const sort = () => {
        const nextDir = th.dataset.sortDir === "asc" ? "desc" : "asc";
        headers.forEach((h) => {
          delete h.dataset.sortDir;
          h.classList.remove("sort-asc", "sort-desc");
          h.removeAttribute("aria-sort");
        });
        th.dataset.sortDir = nextDir;
        th.classList.add(nextDir === "asc" ? "sort-asc" : "sort-desc");
        th.setAttribute("aria-sort", nextDir === "asc" ? "ascending" : "descending");

        const isNumber = th.dataset.type === "number";
        const factor = nextDir === "asc" ? 1 : -1;
        const sorted = rows.slice().sort((a, b) => {
          const av = (a.cells[index] ? a.cells[index].textContent : "").trim();
          const bv = (b.cells[index] ? b.cells[index].textContent : "").trim();
          if (isNumber) {
            const an = parseFloat(av.replace(/[^0-9.-]/g, ""));
            const bn = parseFloat(bv.replace(/[^0-9.-]/g, ""));
            return factor * ((isNaN(an) ? -Infinity : an) - (isNaN(bn) ? -Infinity : bn));
          }
          return factor * av.localeCompare(bv, undefined, { numeric: true, sensitivity: "base" });
        });
        sorted.forEach((row) => tbody.appendChild(row));
      };

      button.addEventListener("click", sort);
    });
  });
}

// SSO connection form (sso/form.html): show the fields for the chosen protocol. The hidden
// fieldset is also disabled, so its required fields don't block submitting the other one.
document.querySelectorAll("select[data-toggle-protocol]").forEach((select) => {
  const form = select.closest("form");
  const show = () => {
    form.querySelectorAll("fieldset[data-protocol]").forEach((fieldset) => {
      const active = fieldset.dataset.protocol === select.value;
      fieldset.hidden = !active;
      fieldset.disabled = !active;
    });
  };
  select.addEventListener("change", show);
  show();
});


// Embedded in a bank's online banking (services/embed_service.py): tell the parent page
// (only the origin the session was started for) the page's height, where it is, and what
// just happened, so @openclear/embed can size the frame and pass events on. Events are
// hints for the page's UI; the bank's systems should act on webhooks or the API.
(() => {
  const parentOrigin = document.body.dataset.embedParent;
  if (!parentOrigin || window.parent === window) return;
  const post = (type, detail = {}) => window.parent.postMessage({ source: "openclear", type, ...detail }, parentOrigin);
  let lastHeight = 0;
  const report = () => {
    const height = Math.ceil(document.documentElement.scrollHeight);
    if (height !== lastHeight) {
      lastHeight = height;
      post("resize", { height });
    }
  };
  new ResizeObserver(report).observe(document.body);
  report();
  post("navigated", { path: location.pathname, title: document.title });
  const event = document.body.dataset.embedEvent;
  if (event) {
    const match = location.pathname.match(/^\/ui\/exceptions\/([0-9a-f-]{36})/);
    post(event, match ? { exceptionId: match[1] } : {});
  }
})();


// --- Shared components (UX_PLAN.md U12) ------------------------------------------------------

// askToConfirm(message) opens the page's confirmation dialog (base.html) and resolves to
// true or false. Falls back to the browser's own confirm() where <dialog> isn't supported.
function askToConfirm(message, options = {}) {
  const dialog = document.getElementById("confirm-dialog");
  if (!dialog || typeof dialog.showModal !== "function") return Promise.resolve(window.confirm(message));
  dialog.querySelector("#confirm-dialog-title").textContent = options.title || "Are you sure?";
  dialog.querySelector("[data-confirm-message]").textContent = message;
  const yes = dialog.querySelector("[data-confirm-yes]");
  const no = dialog.querySelector("[data-confirm-no]");
  yes.textContent = options.yes || "Continue";
  yes.classList.toggle("danger", !!options.danger);
  const opener = document.activeElement;
  return new Promise((resolve) => {
    const finish = (answer) => {
      yes.removeEventListener("click", onYes);
      no.removeEventListener("click", onNo);
      dialog.removeEventListener("close", onClose);
      if (dialog.open) dialog.close();
      if (opener && opener.focus) opener.focus();
      resolve(answer);
    };
    const onYes = () => finish(true);
    const onNo = () => finish(false);
    const onClose = () => finish(false); // Escape
    yes.addEventListener("click", onYes);
    no.addEventListener("click", onNo);
    dialog.addEventListener("close", onClose);
    dialog.showModal();
    no.focus(); // the safe choice has focus first
  });
}

// Submits a form once confirmed, keeping which button was pressed (its name and value).
function submitConfirmed(form, submitter) {
  form.dataset.confirmed = "1";
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit(submitter && submitter.form === form ? submitter : undefined);
  } else {
    form.submit();
  }
}

// A form (or a submit button) with data-confirm="message" asks first. Add
// data-confirm-yes="Void it" for the button's text and data-confirm-danger for a destructive one.
function initConfirmations() {
  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.confirmed === "1") return;
      const source = event.submitter && event.submitter.dataset.confirm ? event.submitter : form;
      const message = source.dataset.confirm;
      if (!message) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      askToConfirm(message, {
        title: source.dataset.confirmTitle, yes: source.dataset.confirmYes, danger: source.hasAttribute("data-confirm-danger"),
      }).then((ok) => {
        if (ok) submitConfirmed(form, event.submitter);
      });
    }, true);
  });
}

// While a form is being sent, its submit buttons are disabled and say so, so a slow
// upload or export can't be sent twice. Opt out with data-no-busy. A page shown again from
// the back/forward cache gets its buttons back.
function initBusyForms() {
  document.querySelectorAll("form").forEach((form) => {
    if (form.hasAttribute("data-no-busy") || (form.method || "get").toLowerCase() !== "post") return;
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      form.setAttribute("aria-busy", "true");
      form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]').forEach((button) => {
        // Disabled after this tick, so the pressed button's name and value are still sent.
        setTimeout(() => {
          button.disabled = true;
          if (button.tagName === "BUTTON" && !button.dataset.idleLabel) {
            button.dataset.idleLabel = button.innerHTML;
            button.textContent = button.dataset.busyLabel || "Working…";
          }
        }, 0);
      });
    });
  });
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    document.querySelectorAll("form[aria-busy]").forEach((form) => {
      form.removeAttribute("aria-busy");
      form.dataset.confirmed = "";
      form.querySelectorAll("button[data-idle-label]").forEach((button) => {
        button.innerHTML = button.dataset.idleLabel;
        delete button.dataset.idleLabel;
        button.disabled = false;
      });
    });
  });
}

// A "saved" message (data-toast) moves to the corner, is announced politely, and fades
// after a few seconds. Errors stay where they are.
function initToasts() {
  const region = document.getElementById("toast-region");
  if (!region) return;
  document.querySelectorAll("[data-toast]").forEach((el) => {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = el.textContent.trim();
    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "×";
    close.addEventListener("click", () => toast.remove());
    toast.appendChild(close);
    region.appendChild(toast);
    el.remove();
    setTimeout(() => toast.remove(), 8000);
  });
}

// An element with data-poll-seconds (a job still running) reloads the page until the
// server stops rendering it, i.e. when the job is done.
function initPolling() {
  const el = document.querySelector("[data-poll-seconds]");
  if (!el) return;
  const seconds = Math.max(2, parseInt(el.dataset.pollSeconds, 10) || 3);
  setTimeout(() => window.location.reload(), seconds * 1000);
}


// --- Deciding exceptions (UX_PLAN.md U4) -------------------------------------------------------

function typingInField(event) {
  const el = event.target;
  return el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) && el.type !== "checkbox";
}

// Keys: on an exception's page, P pays (asking first where it would anyway) and R opens
// the return reasons; on the list, J and K move between rows and Enter opens one.
function initDecisionKeys() {
  const panel = document.querySelector("[data-decision-panel]");
  const rows = Array.from(document.querySelectorAll("tr[data-row-link]"));
  if (!panel && !rows.length) return;
  let current = -1;
  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey || event.metaKey || event.altKey || typingInField(event) || document.querySelector("dialog[open]")) return;
    const key = event.key.toLowerCase();
    if (panel && key === "p") {
      const pay = panel.querySelector('[data-key="p"]');
      if (pay) { event.preventDefault(); pay.click(); }
    } else if (panel && key === "r") {
      const ret = panel.querySelector("details.decision-return");
      if (ret) {
        event.preventDefault();
        ret.open = true;
        const first = ret.querySelector('input[type="radio"], input[type="text"]');
        if (first) first.focus();
      }
    } else if (rows.length && (key === "j" || key === "k")) {
      event.preventDefault();
      current = Math.max(0, Math.min(rows.length - 1, current + (key === "j" ? 1 : -1)));
      rows.forEach((row, i) => row.classList.toggle("row-focused", i === current));
      const link = rows[current].querySelector("a[href]");
      if (link) link.focus();
    }
  });
}

// The exceptions list: tick items, then Pay or Return them together. The bar says how
// many and how much, and the confirmation repeats it.
function initBulkDecisions() {
  const form = document.getElementById("bulk-form");
  if (!form) return;
  const boxes = Array.from(document.querySelectorAll('input[name="exception_ids"][form="bulk-form"]'));
  const bar = form.querySelector("[data-bulk-bar]");
  const all = document.querySelector("[data-select-all]");
  const money = (n) => n.toLocaleString(undefined, { style: "currency", currency: "USD" });
  const update = () => {
    const chosen = boxes.filter((b) => b.checked);
    const total = chosen.reduce((sum, b) => sum + (parseFloat(b.dataset.amount) || 0), 0);
    bar.hidden = chosen.length === 0;
    form.querySelector("[data-bulk-count]").textContent = `${chosen.length} selected · ${money(total)}`;
    const noun = chosen.length === 1 ? "item" : "items";
    form.querySelectorAll('[data-bulk-outcome]').forEach((button) => {
      const verb = button.dataset.bulkOutcome === "pay" ? "Pay" : "Return";
      button.dataset.confirm = `${verb} ${chosen.length} ${noun}, ${money(total)} in all? Each is decided exactly as it would be on its own page.`;
      button.dataset.confirmTitle = `${verb} these ${noun}?`;
      button.dataset.confirmYes = `${verb} ${chosen.length}`;
    });
    if (all) all.checked = chosen.length === boxes.length && boxes.length > 0;
  };
  boxes.forEach((b) => b.addEventListener("change", update));
  if (all) all.addEventListener("change", () => { boxes.forEach((b) => (b.checked = all.checked)); update(); });
  update();
}

// A file input with data-dropzone also takes a file dragged onto it (UX_PLAN.md U2). The
// input stays a real, labelled file input, so the keyboard and screen readers use it as
// before; the zone around it shows the chosen file's name.
function initDropzones() {
  document.querySelectorAll("input[type=file][data-dropzone]").forEach((input) => {
    const zone = document.createElement("div");
    zone.className = "dropzone";
    input.parentNode.insertBefore(zone, input);
    zone.appendChild(input);
    const hint = document.createElement("p");
    hint.className = "dropzone-hint";
    hint.setAttribute("aria-live", "polite");
    hint.textContent = "or drag a file here";
    zone.appendChild(hint);
    const show = () => {
      hint.textContent = input.files && input.files.length ? `Chosen: ${input.files[0].name}` : "or drag a file here";
    };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((name) => zone.addEventListener(name, (event) => {
      event.preventDefault();
      zone.classList.add("dropzone-over");
    }));
    ["dragleave", "drop"].forEach((name) => zone.addEventListener(name, () => zone.classList.remove("dropzone-over")));
    zone.addEventListener("drop", (event) => {
      event.preventDefault();
      if (!event.dataTransfer || !event.dataTransfer.files.length) return;
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });
}

// An element with data-lazy-state="<url>" shows a state worked out after the page loads
// (it takes a moment): the URL answers {"state": "1 WARN", "tone": "warn"}.
function initLazyStates() {
  document.querySelectorAll("[data-lazy-state]").forEach((el) => {
    fetch(el.dataset.lazyState, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        el.textContent = data ? data.state : "—";
        if (data && data.tone) el.classList.add(`tone-${data.tone}`);
      })
      .catch(() => { el.textContent = "—"; });
  });
}

