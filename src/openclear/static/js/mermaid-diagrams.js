// Loaded only on pages with Mermaid diagrams (see templates/docs/admin/data-dictionary.html's
// {% block scripts %} override) -- kept out of app.js so the vendored Mermaid bundle never
// loads on any other page. Renders every <pre class="mermaid"> block (see
// templates/_macros/mermaid_diagram.html) and wires each one's own zoom in/out/reset buttons
// to a dedicated svg-pan-zoom instance on its rendered SVG.
document.addEventListener("DOMContentLoaded", () => {
  const nodes = document.querySelectorAll(".mermaid-container .mermaid");
  if (nodes.length === 0 || typeof mermaid === "undefined") return;

  // Mirrors app.css's own dark-mode convention exactly (static/css/app.css's
  // :root[data-theme="dark"] / prefers-color-scheme rules) so a diagram matches whatever
  // mode the page already loaded in -- no reactive re-render needed, since toggling the
  // theme already triggers a full page reload (web/routers/theme.py).
  const forced = document.documentElement.getAttribute("data-theme");
  const isDark = forced === "dark" || (forced !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  mermaid.initialize({ startOnLoad: false, theme: isDark ? "dark" : "default" });

  mermaid.run({ querySelector: ".mermaid" }).then(() => {
    document.querySelectorAll(".mermaid-wrap").forEach((wrap) => {
      // Scoped to .mermaid-container specifically -- a bare "svg" selector here would
      // match the toolbar's own icon <svg> elements first (they come earlier in the DOM
      // than the rendered diagram), attaching pan-zoom to a tiny 16px icon instead of the
      // actual diagram.
      const container = wrap.querySelector(".mermaid-container");
      const svg = container?.querySelector("svg");
      if (!svg) return;

      // scripts/render_schema_diagrams.py sets this global before navigating here (via
      // Playwright's add_init_script, so it's in place before this script even runs). It
      // needs every diagram rendered at its full natural size -- not clamped to this
      // page's small preview box, and not pan/zoom-transformed -- so it can screenshot
      // each one whole (foreignObject text and all -- see below) for the PDF export's own
      // dedicated diagram pages -- captured as a real browser screenshot rather than
      // extracted SVG markup, since Mermaid's entity/column labels are HTML rendered via
      // <foreignObject>, which WeasyPrint's own (non-browser) SVG engine doesn't support
      // and would silently render as blank boxes. svg.getBBox() measures the actual
      // rendered vector content's geometry directly, regardless of the outer <svg>'s
      // current width/height/viewport.
      if (window.__openclearSkipPanZoom) {
        const bbox = svg.getBBox();
        svg.setAttribute("viewBox", `${bbox.x} ${bbox.y} ${bbox.width} ${bbox.height}`);
        svg.setAttribute("width", `${Math.ceil(bbox.width)}px`);
        svg.setAttribute("height", `${Math.ceil(bbox.height)}px`);
        container.style.height = "auto";
        container.style.overflow = "visible";
        // The toolbar is a sibling overlay, positioned absolutely over the container's
        // top-right corner -- an element screenshot crops by on-screen pixel position, so
        // without this its icons would bleed into the top corner of the captured image.
        wrap.querySelector(".mermaid-toolbar")?.style.setProperty("display", "none");
        return;
      }

      // Mermaid's ER diagrams ship with a width="100%" attribute but no height attribute
      // and no viewBox -- with no intrinsic aspect ratio to resolve against, a PERCENTAGE
      // height (whether set via this attribute or the CSS height:100% rule on
      // .mermaid-container svg) never resolves; the SVG falls back to the browser's
      // default replaced-element height (150px), a thin sliver at the top of the
      // container. Only an explicit pixel height actually works. This matters beyond
      // looks: pan/zoom only responds to mouse wheel/drag where the real SVG element's
      // box is, so with the 150px sliver, every interaction aimed at the visible
      // (larger, CSS-stretched-looking) card missed the actual element entirely.
      svg.setAttribute("width", `${container.clientWidth}px`);
      svg.setAttribute("height", `${container.clientHeight}px`);
      const panZoom = svgPanZoom(svg, {
        controlIconsEnabled: false, zoomEnabled: true, panEnabled: true, fit: true, center: true,
        minZoom: 0.2, maxZoom: 10,
      });
      wrap.querySelector(".mermaid-zoom-in")?.addEventListener("click", () => panZoom.zoomIn());
      wrap.querySelector(".mermaid-zoom-out")?.addEventListener("click", () => panZoom.zoomOut());
      wrap.querySelector(".mermaid-zoom-reset")?.addEventListener("click", () => panZoom.reset());
    });
  });
});
