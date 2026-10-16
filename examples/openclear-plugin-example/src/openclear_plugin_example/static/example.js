// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Chaffed

// The example plugin's script, loaded only on pages where its vendor score panel shows: it
// marks a high score so it stands out. A plugin's own static file, as the CSP allows.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".example-score").forEach((el) => {
    if (Number(el.textContent) >= 50) el.classList.add("example-score-high");
    el.setAttribute("data-example-ready", "");
  });
});
