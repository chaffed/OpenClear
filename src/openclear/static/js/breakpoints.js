// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Chaffed

// The two breakpoints (MOBILE_PLAN.md §1), read from app.css's --bp-phone-max and
// --bp-tablet-max so they're defined once. OpenClear.breakpoint("phone") or ("tablet") is a
// MediaQueryList: .matches below that width, and a "change" event when it flips.
window.OpenClear = window.OpenClear || {};
window.OpenClear.breakpoint = (name) => {
  const max = getComputedStyle(document.documentElement).getPropertyValue(`--bp-${name}-max`).trim();
  return window.matchMedia(`(max-width: ${max || (name === "phone" ? "639.98px" : "1023.98px")})`);
};

// Two panes (the exception queue beside the chosen item, MOBILE_PLAN.md R2): a touch screen
// held sideways, at any width; callers leave phones out with breakpoint("phone").
window.OpenClear.twoPane = () => window.matchMedia("(pointer: coarse) and (orientation: landscape)");
