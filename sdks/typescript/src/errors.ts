// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/** Every error the SDK throws is an OpenClearError; the subclasses mark the cases that call for different action. */
export class OpenClearError extends Error {
  readonly status: number | undefined;
  readonly detail: unknown;
  readonly body: unknown;

  constructor(message: string, options: { status?: number; detail?: unknown; body?: unknown; cause?: unknown } = {}) {
    super(message, { cause: options.cause });
    this.name = new.target.name;
    this.status = options.status;
    this.detail = options.detail;
    this.body = options.body;
  }
}

/** No response: the server couldn't be reached or didn't answer in time (after retries). */
export class APIConnectionError extends OpenClearError {}
/** 401: the credentials or token were refused. */
export class AuthenticationError extends OpenClearError {}
/** 403: authenticated, but not allowed to do this. */
export class PermissionDeniedError extends OpenClearError {}
/** 404: no such item, or not one this client can see. */
export class NotFoundError extends OpenClearError {}
/** 409: conflicts with the current state. */
export class ConflictError extends OpenClearError {}
/** 422: refused; `errors` lists field problems when the server gave them. */
export class ValidationError extends OpenClearError {
  get errors(): Array<Record<string, unknown>> {
    return Array.isArray(this.detail) ? (this.detail as Array<Record<string, unknown>>) : [];
  }
}
/** 429: too many requests (after retries). */
export class RateLimitError extends OpenClearError {
  retryAfter: number | undefined;
}
/** 5xx: OpenClear failed (after retries). */
export class ServerError extends OpenClearError {}
/** A webhook's signature or timestamp didn't check out. */
export class WebhookVerificationError extends OpenClearError {}
/** A bulk upload was rejected or its import failed; `upload` is its final status. */
export class BulkUploadError extends OpenClearError {
  upload: Record<string, unknown> | undefined;
}

const BY_STATUS: Record<number, typeof OpenClearError> = {
  401: AuthenticationError,
  403: PermissionDeniedError,
  404: NotFoundError,
  409: ConflictError,
  422: ValidationError,
  429: RateLimitError,
};

export function errorFor(status: number, body: unknown): OpenClearError {
  let detail: unknown;
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    detail = b.detail ?? b.error_description ?? b.error;
  }
  let message: string;
  if (Array.isArray(detail)) {
    message = detail
      .map((d) => {
        if (d && typeof d === "object") {
          const loc = Array.isArray((d as { loc?: unknown[] }).loc) ? (d as { loc: unknown[] }).loc.slice(1).join(".") : "";
          return `${loc || "request"}: ${(d as { msg?: string }).msg ?? JSON.stringify(d)}`;
        }
        return String(d);
      })
      .join("; ");
  } else {
    message = detail ? String(detail) : typeof body === "string" && body ? body : `HTTP ${status}`;
  }
  const Cls = BY_STATUS[status] ?? (status >= 500 ? ServerError : OpenClearError);
  return new Cls(`${status}: ${message}`, { status, detail, body });
}
