// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/**
 * @openclear/embed: shows OpenClear's pages inside your online banking (SDK_PLAN.md P2).
 *
 * Your server gets a one-time launch URL for the signed-in person (`POST /api/v1/embed/launch`,
 * or `oc.forPerson(idToken).embed.createLaunch(...)` with @openclear/sdk); your page mounts it:
 *
 *     import { mount } from "@openclear/embed";
 *     const openclear = mount(document.getElementById("positive-pay")!, {
 *       url: launchUrl,
 *       onEvent: (event) => { if (event.type === "exception-decided") refreshBalances(); },
 *       onSessionEnded: async () => openclear.navigate(await fetchNewLaunchUrl()),
 *     });
 *
 * The frame resizes to its content. Only messages from that frame, from OpenClear's own
 * origin, are listened to. Treat events as hints for your page; act on webhooks or the API.
 */

export type OpenClearEventType = "resize" | "navigated" | "exception-decided" | "exception-recommended" | "session-ended";

export interface OpenClearEvent {
  type: OpenClearEventType;
  height?: number;
  path?: string;
  title?: string;
  exceptionId?: string;
}

export interface MountOptions {
  /** The launch URL from POST /api/v1/embed/launch (good for a minute, once). */
  url: string;
  /** Lower bound for the frame's height, in pixels (default 400). */
  minHeight?: number;
  /** The frame's accessible name (default "OpenClear"). */
  title?: string;
  /** Every event from the frame, including resizes. */
  onEvent?: (event: OpenClearEvent) => void;
  /** The session ended (signed out, timed out, or access removed): start a new one. */
  onSessionEnded?: () => void;
}

export interface EmbeddedOpenClear {
  readonly frame: HTMLIFrameElement;
  /** Opens another launch URL in the same frame (after the session ended, say). */
  navigate(url: string): void;
  /** Removes the frame and stops listening. */
  destroy(): void;
}

/** What the frame's message means, or null if it isn't one to trust (exported for tests). */
export function readMessage(
  event: { origin: string; source: unknown; data: unknown },
  expected: { origin: string; window: unknown },
): OpenClearEvent | null {
  if (event.origin !== expected.origin || event.source !== expected.window) return null;
  const data = event.data as Record<string, unknown> | null;
  if (!data || typeof data !== "object" || data.source !== "openclear" || typeof data.type !== "string") return null;
  const known: OpenClearEventType[] = ["resize", "navigated", "exception-decided", "exception-recommended", "session-ended"];
  if (!known.includes(data.type as OpenClearEventType)) return null;
  const out: OpenClearEvent = { type: data.type as OpenClearEventType };
  if (typeof data.height === "number" && Number.isFinite(data.height)) out.height = Math.max(0, Math.min(data.height, 100_000));
  if (typeof data.path === "string") out.path = data.path.slice(0, 500);
  if (typeof data.title === "string") out.title = data.title.slice(0, 200);
  if (typeof data.exceptionId === "string" && /^[0-9a-f-]{36}$/.test(data.exceptionId)) out.exceptionId = data.exceptionId;
  return out;
}

export function mount(container: HTMLElement, options: MountOptions): EmbeddedOpenClear {
  let origin = new URL(options.url).origin;
  const minHeight = options.minHeight ?? 400;
  const frame = document.createElement("iframe");
  frame.title = options.title ?? "OpenClear";
  frame.src = options.url;
  frame.style.width = "100%";
  frame.style.border = "0";
  frame.style.height = `${minHeight}px`;
  frame.referrerPolicy = "strict-origin-when-cross-origin";
  // Scripts, forms and its own cookies; downloads for exports; new windows for documents.
  frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-popups-to-escape-sandbox");
  frame.setAttribute("allow", "publickey-credentials-get *");
  container.appendChild(frame);

  const listener = (event: MessageEvent) => {
    const message = readMessage(event, { origin, window: frame.contentWindow });
    if (!message) return;
    if (message.type === "resize" && message.height !== undefined) {
      frame.style.height = `${Math.max(minHeight, message.height)}px`;
    }
    options.onEvent?.(message);
    if (message.type === "session-ended") options.onSessionEnded?.();
  };
  window.addEventListener("message", listener);

  return {
    frame,
    navigate(url: string) {
      origin = new URL(url).origin;
      frame.src = url;
    },
    destroy() {
      window.removeEventListener("message", listener);
      frame.remove();
    },
  };
}
