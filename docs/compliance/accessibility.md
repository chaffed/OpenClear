# Accessibility

*Current as of 2026-10-22. Checked against its sources at least once a year (control check `regulatory_currency`).*

OpenClear aims to meet WCAG 2.2 Level AA. This page says what's checked automatically, what
isn't yet, and what a VPAT for OpenClear would contain. It isn't legal advice
([Compliance](README.md)).

**Who it binds.** WCAG is a W3C standard and binds nobody by itself. Section 508 binds federal
agencies buying technology, and its standard is WCAG 2.0 AA; a state-chartered bank meets it only
as the vocabulary of a VPAT. ADA Title III covers the bank as a public accommodation, but the
Department of Justice has no technical web rule for businesses; it and the courts point to WCAG.
Its Title II rule (WCAG 2.1 AA) covers state and local governments only, from April 2027. A bank's
procurement may ask for a VPAT (CUEC B-SA-4-01).

## Checked on every build

- **axe-core** in Chromium, for WCAG 2.0, 2.1 and 2.2 at levels A and AA, on 22 of the bank's
  pages (including the in-app guides and the data dictionary) and 9 of a customer's, in light and
  dark themes, on a desktop and a phone, with three accent colours.
- **73 pages at 360, 768, 1024 and 1280px**: axe again, no sideways scroll, touch targets of at
  least 44px below desktop (and WCAG 2.2's 24px everywhere, through axe), Pay and Return in view on
  an exception, and a control focused from the keyboard never hidden behind a bar stuck to the
  bottom of the screen.
- A new problem fails the build. In the markup: a skip link, visible focus, reduced motion
  respected, and `autocomplete` on the sign-in and password forms.

## WCAG 2.2's new criteria

| Criterion | Status |
|---|---|
| 2.4.11 Focus Not Obscured (Minimum), AA | Supports: the page scrolls a focused control clear of the decision bar and the bulk-action bar; the decision bar is checked on every build |
| 2.5.7 Dragging Movements, AA | Supports: the file drop zone is also a file button; nothing else drags |
| 2.5.8 Target Size (Minimum), AA | Supports: 24px at every width (checked by axe), 44px below desktop |
| 3.2.6 Consistent Help, A | Supports: help is in the same place on every page |
| 3.3.7 Redundant Entry, A | Not yet evaluated |
| 3.3.8 Accessible Authentication (Minimum), AA | Supports: password managers and paste work, no puzzle, security keys and passkeys, SSO |

## Known gaps

- No manual pass with a screen reader (VoiceOver, NVDA) or the keyboard alone has been done. Automated
  checks find about a third of WCAG's problems; the rest (meaningful structure, focus order, error
  text, status messages) need a person. It's parked until a tester is available
  (`plans/PARKED_PLAN.md`).
- Some pages have more than one top-level heading (`plans/FULL_REVIEW_PLAN.md` U7).

## A VPAT

ITI's VPAT 2.5Rev (April 2025): the WCAG edition (WCAG 2.2 A and AA), or the INT edition for a bank
that asks for Section 508 too. It gives the product and version; how it was evaluated (the
automated checks above and the manual pass); each criterion as Supports, Partially Supports, Does
Not Support or Not Applicable, with remarks; and the software (Chapter 5) and documentation and
support (Chapter 6) sections. OpenClear will publish one once the manual pass is done.

## Sources

Read 22 October 2026: WCAG 2.2 (W3C Recommendation, 12 December 2024) and *Understanding 2.5.8*;
36 CFR Part 1194 (US Access Board, amended January 2018); ITI's VPAT 2.5Rev (April 2025); the
Department of Justice's *Guidance on Web Accessibility and the ADA* (March 2022); 28 CFR Part 35
Subpart H and the interim final rule of 20 April 2026 extending its dates.
