// Loaded only on pages with a templates/_macros/markdown_editor.html instance (currently
// settings/form.html's Messages card and customers/banner.html) -- kept out of app.js so
// it never loads on every other page. Wires up each [data-markdown-editor]'s formatting
// toolbar, image insert, and live preview. No build step / vendored library: this is a
// small enough need (wrap-selection-in-markdown-syntax) that a real editor library would
// be overkill for this app's existing zero-Node-build-step convention -- see
// mermaid-diagrams.js for the one case where vendoring was actually justified.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-markdown-editor]").forEach(initMarkdownEditor);
});

const MAX_IMAGE_BYTES = 150 * 1024; // must match services/message_content.py's own cap -- this is a UX head start, not the real check
const PREVIEW_DEBOUNCE_MS = 300;

function initMarkdownEditor(wrap) {
  const textarea = wrap.querySelector(".md-editor-textarea");
  const preview = wrap.querySelector(".md-editor-preview");
  if (!textarea || !preview) return;

  wrap.querySelectorAll(".md-editor-btn[data-md-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.mdAction;
      if (action === "bold") wrapSelection(textarea, "**", "**");
      else if (action === "italic") wrapSelection(textarea, "_", "_");
      else if (action === "link") insertLink(textarea);
      else if (action === "list") prefixLines(textarea, "- ");
      else if (action === "image") wrap.querySelector(".md-editor-file-input")?.click();
    });
  });

  wrap.querySelector(".md-editor-file-input")?.addEventListener("change", (event) => {
    handleImageFile(textarea, event.target);
  });

  const requestPreview = debounce(() => updatePreview(textarea, preview), PREVIEW_DEBOUNCE_MS);
  // setRangeText() (used by every toolbar action and the image insert below) doesn't
  // dispatch an "input" event the way real typing does -- each of those calls this
  // directly afterward so the preview stays in sync regardless of how the text changed.
  textarea.addEventListener("input", requestPreview);
  textarea._requestPreview = requestPreview;
  updatePreview(textarea, preview); // show whatever's already in the textarea on load
}

function wrapSelection(textarea, before, after) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.slice(start, end);
  textarea.setRangeText(`${before}${selected}${after}`, start, end, "select");
  if (!selected) {
    textarea.selectionStart = textarea.selectionEnd = start + before.length;
  }
  textarea.focus();
  textarea._requestPreview?.();
}

function prefixLines(textarea, prefix) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.slice(start, end) || "list item";
  const prefixed = selected.split("\n").map((line) => `${prefix}${line}`).join("\n");
  textarea.setRangeText(prefixed, start, end, "select");
  textarea.focus();
  textarea._requestPreview?.();
}

function insertLink(textarea) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.slice(start, end) || "link text";
  const url = window.prompt("Link URL:", "https://");
  if (!url) return;
  textarea.setRangeText(`[${selected}](${url})`, start, end, "select");
  textarea.focus();
  textarea._requestPreview?.();
}

function handleImageFile(textarea, fileInput) {
  const file = fileInput.files[0];
  fileInput.value = ""; // reset so choosing the same file again still fires "change"
  if (!file) return;

  if (file.size > MAX_IMAGE_BYTES) {
    window.alert(`That image is too large (max ${Math.round(MAX_IMAGE_BYTES / 1024)} KB) -- the server would reject it too.`);
    return;
  }

  const reader = new FileReader();
  reader.onload = () => {
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    textarea.setRangeText(`![](${reader.result})`, start, end, "end");
    textarea.focus();
    textarea._requestPreview?.();
  };
  reader.readAsDataURL(file);
}

function updatePreview(textarea, preview) {
  const body = new URLSearchParams({ text: textarea.value });
  fetch("/ui/markdown-preview", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body })
    .then((response) => response.text())
    .then((html) => {
      preview.innerHTML = html;
    })
    .catch(() => {
      // Best-effort UI aid -- a failed preview request just leaves the previous
      // rendered preview in place rather than surfacing an error for a non-critical
      // convenience feature; the real validation still happens on actual form submit.
    });
}

function debounce(fn, delayMs) {
  let timer = null;
  return (...args) => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => fn(...args), delayMs);
  };
}
