// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/**
 * Checking that a webhook really came from OpenClear (Standard Webhooks). Verify with the
 * endpoint's secret (`whsec_...`) and the raw request body, before parsing it. Uses Web
 * Crypto, so it runs in Node 18+, Deno, Bun, Cloudflare Workers and browsers.
 */
import { WebhookVerificationError } from "./errors.ts";

export const DEFAULT_TOLERANCE_SECONDS = 300;

export interface WebhookEvent {
  type: string;
  timestamp: string;
  data: Record<string, unknown>;
}

type HeaderSource = Headers | Record<string, string | string[] | undefined>;

function header(headers: HeaderSource, name: string): string | undefined {
  if (typeof (headers as Headers).get === "function") return (headers as Headers).get(name) ?? undefined;
  for (const [key, value] of Object.entries(headers as Record<string, string | string[] | undefined>)) {
    if (key.toLowerCase() === name) return Array.isArray(value) ? value[0] : value;
  }
  return undefined;
}

function bytesOf(body: string | Uint8Array | ArrayBuffer): Uint8Array {
  if (typeof body === "string") return new TextEncoder().encode(body);
  return body instanceof Uint8Array ? body : new Uint8Array(body);
}

function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function equal(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** The `webhook-signature` value OpenClear sends (for testing your own receiver). */
export async function sign(secret: string, messageId: string, timestamp: number, body: string | Uint8Array | ArrayBuffer): Promise<string> {
  let keyBytes: Uint8Array;
  try {
    keyBytes = base64ToBytes(secret.replace(/^whsec_/, ""));
  } catch {
    throw new WebhookVerificationError("The secret isn't a valid whsec_ secret.");
  }
  const key = await crypto.subtle.importKey("raw", keyBytes as BufferSource, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const prefix = new TextEncoder().encode(`${messageId}.${timestamp}.`);
  const raw = bytesOf(body);
  const signed = new Uint8Array(prefix.length + raw.length);
  signed.set(prefix);
  signed.set(raw, prefix.length);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", key, signed as BufferSource));
  return `v1,${bytesToBase64(mac)}`;
}

/**
 * Resolves to the event if the signature matches and the timestamp is recent; rejects with
 * WebhookVerificationError otherwise. A retried event keeps its `webhook-id` header, so use
 * it to skip one you've already handled.
 */
export async function verify(
  secret: string,
  headers: HeaderSource,
  body: string | Uint8Array | ArrayBuffer,
  options: { toleranceSeconds?: number; now?: number } = {},
): Promise<WebhookEvent> {
  const messageId = header(headers, "webhook-id");
  const timestamp = header(headers, "webhook-timestamp");
  const signatures = header(headers, "webhook-signature");
  if (!messageId || !timestamp || !signatures) {
    throw new WebhookVerificationError("Missing webhook-id, webhook-timestamp or webhook-signature header.");
  }
  const sentAt = Number(timestamp);
  if (!Number.isInteger(sentAt)) throw new WebhookVerificationError("webhook-timestamp isn't a number.");
  const now = options.now ?? Date.now() / 1000;
  if (Math.abs(now - sentAt) > (options.toleranceSeconds ?? DEFAULT_TOLERANCE_SECONDS)) {
    throw new WebhookVerificationError("The webhook's timestamp is too old or in the future (a replay, or a clock out of sync).");
  }
  const expected = await sign(secret, messageId, sentAt, body);
  if (!signatures.split(" ").some((candidate) => equal(candidate, expected))) {
    throw new WebhookVerificationError("The webhook's signature doesn't match.");
  }
  try {
    return JSON.parse(new TextDecoder().decode(bytesOf(body))) as WebhookEvent;
  } catch {
    throw new WebhookVerificationError("The webhook's body isn't JSON.");
  }
}
