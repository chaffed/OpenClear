// The issue-file layout builder (templates/layouts/form.html). Without this script every
// setting is still there and the server previews the result; with it:
// - only the settings for the chosen kind of file show;
// - in a fixed-width sample, selecting a field's characters and pressing "Use selected
//   text" fills in its start and length.

(function () {
  document.addEventListener("DOMContentLoaded", () => {
    const form = document.querySelector("form.layout-builder");
    if (!form) return;

    const type = form.querySelector("[data-layout-type]");
    const applyType = () => {
      form.querySelectorAll("[data-layout-for]").forEach((el) => {
        el.hidden = !el.dataset.layoutFor.split(" ").includes(type.value);
      });
    };
    type.addEventListener("change", applyType);
    applyType();

    const lines = form.querySelector("[data-sample-lines]");
    const tools = form.querySelector("[data-selection-tools]");
    if (!lines || !tools) return;
    tools.hidden = false;
    const status = tools.querySelector("[data-selection-status]");
    let picked = null; // {start, length}

    // Where a selection starts within its line, counting from 1.
    const offsetInLine = (node, offset) => {
      const line = (node.nodeType === Node.TEXT_NODE ? node.parentElement : node).closest(".sample-line");
      if (!line) return null;
      const range = document.createRange();
      range.setStart(line, 0);
      range.setEnd(node, offset);
      return { line, position: range.toString().length };
    };

    document.addEventListener("selectionchange", () => {
      const selection = window.getSelection();
      if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return;
      const range = selection.getRangeAt(0);
      if (!lines.contains(range.startContainer)) return;
      const start = offsetInLine(range.startContainer, range.startOffset);
      const end = offsetInLine(range.endContainer, range.endOffset);
      if (!start || !end || start.line !== end.line || end.position <= start.position) {
        picked = null;
        status.textContent = "Select characters within one line.";
        return;
      }
      picked = { start: start.position + 1, length: end.position - start.position };
      status.textContent = `Selected: start ${picked.start}, length ${picked.length}.`;
    });

    tools.querySelector("[data-use-selection]").addEventListener("click", () => {
      if (!picked) {
        status.textContent = "First select a field's characters in the sample.";
        return;
      }
      const field = tools.querySelector("[data-selection-field]").value;
      form.querySelector(`[name="start_${field}"]`).value = picked.start;
      form.querySelector(`[name="length_${field}"]`).value = picked.length;
      status.textContent = `Set start ${picked.start} and length ${picked.length}. Press Preview to check.`;
    });
  });
})();
