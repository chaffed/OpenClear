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

  document.querySelectorAll("[data-confirm]").forEach((el) => {
    el.addEventListener("submit", (event) => {
      if (!window.confirm(el.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });

  // A link with data-history-back returns to the previous page (e.g. error.html's "Go
  // back", so a rejected form's typed values are still there) — wired up here rather
  // than as a javascript: href, which the strict script-src CSP blocks. Its real href is
  // the no-JS / no-history fallback.
  // Exception decision forms (exceptions/detail.html): show only the reason field that
  // fits the chosen outcome (e.g. the ACH return-reason list only for Return), and ask
  // for confirmation before paying an item whose fraud risk is High. The server still
  // enforces everything that matters; this only keeps the form uncluttered and slows
  // down an accidental click.
  document.querySelectorAll("[data-decision-form]").forEach((form) => {
    const select = form.querySelector("[data-outcome-select]");
    if (!select) return;
    const sync = () => {
      form.querySelectorAll("[data-show-for-outcome]").forEach((field) => {
        field.hidden = field.dataset.showForOutcome !== select.value;
      });
    };
    select.addEventListener("change", sync);
    sync();
    form.addEventListener("submit", (event) => {
      if (select.value === "pay" && form.dataset.confirmPay && !window.confirm(form.dataset.confirmPay)) {
        event.preventDefault();
      }
    });
  });

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

// Mobile-only hamburger toggle (see base.html's .sidenav-header / #sidenav-panel and
// app.css's @media (max-width: 720px) rules) -- the button and panel only exist so this
// is a no-op at desktop widths where CSS keeps the panel always visible regardless of
// the "open" class. No "close on navigate" handling needed: this is a server-rendered
// multi-page app, so every link click is a full page load that resets the toggle state.
function initNavToggle() {
  const toggle = document.getElementById("nav-toggle");
  const panel = document.getElementById("sidenav-panel");
  if (!toggle || !panel) return;

  toggle.addEventListener("click", () => {
    const isOpen = panel.classList.toggle("open");
    toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
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

    const search = document.createElement("input");
    search.type = "search";
    search.className = "table-search";
    search.placeholder = "Filter...";
    search.setAttribute("aria-label", "Filter table rows");
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
      th.classList.add("sortable-col");
      th.tabIndex = 0;
      th.setAttribute("role", "button");
      th.setAttribute("aria-sort", "none");

      const sort = () => {
        const nextDir = th.dataset.sortDir === "asc" ? "desc" : "asc";
        headers.forEach((h) => {
          delete h.dataset.sortDir;
          h.classList.remove("sort-asc", "sort-desc");
          h.setAttribute("aria-sort", "none");
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

      th.addEventListener("click", sort);
      th.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sort();
        }
      });
    });
  });
}
