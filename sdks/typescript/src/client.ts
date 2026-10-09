// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

import { Bulk } from "./bulk.ts";
import { APIConnectionError, AuthenticationError, OpenClearError, RateLimitError, errorFor } from "./errors.ts";
import { AREAS, DecisionFeedApi as GeneratedDecisionFeedApi } from "./generated.ts";
import type { Acknowledgement } from "./generated.ts";
import type { CallOptions, Transport } from "./transport.ts";

export interface OpenClearOptions {
  /** e.g. "https://openclear.yourbank.example" */
  baseUrl: string;
  /** An API client's credentials (Admin → API clients). The token is fetched and renewed for you. */
  clientId?: string;
  clientSecret?: string;
  /** Or a token you already have. */
  accessToken?: string;
  /** For the platform endpoints. */
  platformApiKey?: string;
  /** Retries for connection failures, 429 and 502–504 (default 3). */
  maxRetries?: number;
  /** Per request (default 30 s). */
  timeoutMs?: number;
  /** Your own fetch (a proxy agent, a test double). */
  fetch?: typeof fetch;
  userAgent?: string;
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);
const RETRY_STATUSES = new Set([429, 502, 503, 504]);
const TOKEN_MARGIN_MS = 60_000;

type GeneratedAreas = { [K in keyof typeof AREAS]: InstanceType<(typeof AREAS)[K]> };

interface TokenState {
  value?: string;
  expiresAt: number;
  fixed: boolean;
  pending?: Promise<string>;
}

/** The decision feed, plus `consume` for the usual core job. */
export class DecisionFeedApi extends GeneratedDecisionFeedApi {
  /** The last sequence `consumer` acknowledged (0 if it never has). */
  async position(consumer: string): Promise<number> {
    const rows = (await this.feedConsumers()) as Array<Record<string, unknown>>;
    const row = rows.find((r) => r.consumer === consumer);
    return row ? Number(row.through_sequence ?? 0) : 0;
  }

  /**
   * Hands every decision after `consumer`'s acknowledged position to `handle`, in order,
   * acknowledging after each page. Resolves to the last sequence handled. If `handle`
   * throws, nothing after the last acknowledged page is acknowledged, so the next run starts
   * again from there: make `handle` safe to repeat.
   */
  async consume(
    consumer: string,
    handle: (decision: Record<string, unknown>) => unknown,
    options: { after?: number; pageSize?: number } = {},
  ): Promise<number> {
    const pageSize = options.pageSize ?? 500;
    let position = options.after ?? (await this.position(consumer));
    for (;;) {
      const page = (await this.decisionFeed({ after: position, limit: pageSize })) as Array<Record<string, unknown>>;
      if (page.length === 0) return position;
      for (const decision of page) await handle(decision);
      position = Number(page[page.length - 1].sequence);
      const ack: Acknowledgement = { consumer, through_sequence: position };
      await this.acknowledge(ack);
      if (page.length < pageSize) return position;
    }
  }
}

/**
 * A connection to one OpenClear installation as one API client. Every write gets an
 * `Idempotency-Key`, kept across retries, so a retried call never acts twice.
 */
export class OpenClear implements Transport {
  readonly baseUrl: string;
  readonly bulk: Bulk;
  readonly decisionFeed: DecisionFeedApi;
  maxRetries: number;
  /** Replaced in tests to skip waiting. */
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  private readonly options: OpenClearOptions;
  private readonly fetchImpl: typeof fetch;
  private token: TokenState;
  private headers: Record<string, string>;

  constructor(options: OpenClearOptions) {
    if (options.clientId && !options.clientSecret) throw new Error("clientSecret is needed with clientId.");
    this.options = options;
    this.baseUrl = options.baseUrl.replace(/\/+$/, "");
    this.maxRetries = options.maxRetries ?? 3;
    this.fetchImpl = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.token = { value: options.accessToken, expiresAt: options.accessToken ? Infinity : 0, fixed: Boolean(options.accessToken) };
    this.headers = { Accept: "application/json", "User-Agent": options.userAgent ?? "openclear-sdk-js" };
    for (const [name, Area] of Object.entries(AREAS)) {
      (this as Record<string, unknown>)[name] = new Area(this);
    }
    this.decisionFeed = new DecisionFeedApi(this);
    this.bulk = new Bulk(this);
  }

  /**
   * A client that acts for one of the bank's people (`OpenClear-Act-As`): only for an API
   * client the bank allowed to. It can do what both the client and the person may do, and
   * the action is recorded as theirs.
   */
  actingAs(email: string, options: { customerNumber?: string } = {}): OpenClear {
    const other = new OpenClear(this.options);
    other.token = this.token; // shared, so one renewal serves both
    other.sleep = this.sleep;
    other.maxRetries = this.maxRetries;
    other.headers = { ...this.headers, "OpenClear-Act-As": email };
    if (options.customerNumber) other.headers["OpenClear-Act-As-Customer"] = options.customerNumber;
    return other;
  }

  /**
   * Like `actingAs`, naming the person by your online banking platform's own id for them
   * (`OpenClear-Act-As-Subject`). Needs the client linked to your sign-on connection.
   */
  actingAsSubject(subject: string): OpenClear {
    const other = this.actingAs("");
    delete other.headers["OpenClear-Act-As"];
    other.headers["OpenClear-Act-As-Subject"] = subject;
    return other;
  }

  /**
   * Token exchange (RFC 8693): trades the ID token of a person signed in to your online
   * banking platform for a client that acts for them, with what both they and this client
   * may do. It expires when the ID token does; exchange again after that.
   */
  async forPerson(idToken: string): Promise<OpenClear> {
    const response = await this.send("POST", "/api/v1/oauth/token", {
      auth: "none",
      form: {
        grant_type: "urn:ietf:params:oauth:grant-type:token-exchange",
        client_id: this.options.clientId,
        client_secret: this.options.clientSecret,
        subject_token: idToken,
        subject_token_type: "urn:ietf:params:oauth:token-type:id_token",
      },
    }, false);
    const body = (await response.json()) as { access_token: string };
    const other = new OpenClear({ ...this.options, accessToken: body.access_token });
    other.sleep = this.sleep;
    other.maxRetries = this.maxRetries;
    other.headers = { ...this.headers };
    return other;
  }

  // --- Authentication -------------------------------------------------------------------

  private async bearer(refresh = false): Promise<string> {
    const token = this.token;
    if (token.fixed) {
      if (!token.value) throw new AuthenticationError("No access token.");
      return token.value;
    }
    if (!this.options.clientId) throw new AuthenticationError("Pass clientId and clientSecret, or an accessToken.");
    if (!refresh && token.value && Date.now() < token.expiresAt - TOKEN_MARGIN_MS) return token.value;
    if (!token.pending) {
      token.pending = (async () => {
        try {
          const response = await this.send("POST", "/api/v1/oauth/token", {
            auth: "none",
            form: { grant_type: "client_credentials", client_id: this.options.clientId, client_secret: this.options.clientSecret },
          }, false);
          const body = (await response.json()) as { access_token: string; expires_in?: number };
          token.value = body.access_token;
          token.expiresAt = Date.now() + (body.expires_in ?? 1800) * 1000;
          return body.access_token;
        } finally {
          token.pending = undefined;
        }
      })();
    }
    return token.pending;
  }

  private async authHeaders(auth: CallOptions["auth"]): Promise<Record<string, string>> {
    if (auth === "none") return {};
    if (auth === "platform") {
      if (!this.options.platformApiKey) throw new AuthenticationError("This is a platform endpoint: pass platformApiKey.");
      return { "X-Api-Key": this.options.platformApiKey };
    }
    return { Authorization: `Bearer ${await this.bearer()}` };
  }

  // --- Sending --------------------------------------------------------------------------

  private url(path: string, query?: Record<string, unknown>): string {
    if (/^https?:\/\//.test(path)) {
      // A Link header's next page: keep only its path and query, so it can't send the next
      // request to another host.
      const parsed = new URL(path);
      path = parsed.pathname + parsed.search;
    }
    const url = new URL(this.baseUrl + path);
    for (const [key, value] of Object.entries(query ?? {})) {
      if (value === undefined || value === null) continue;
      for (const v of Array.isArray(value) ? value : [value]) url.searchParams.append(key, String(v));
    }
    return url.toString();
  }

  private body(options: CallOptions, headers: Record<string, string>): BodyInit | undefined {
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      return JSON.stringify(options.json);
    }
    if (options.multipart) {
      const form = new FormData();
      for (const [key, value] of Object.entries(options.multipart)) {
        if (value === undefined || value === null) continue;
        if (value instanceof Blob) form.append(key, value, (value as File).name ?? key);
        else form.append(key, String(value));
      }
      return form;
    }
    if (options.form) {
      const form = new URLSearchParams();
      for (const [key, value] of Object.entries(options.form)) {
        if (value !== undefined && value !== null) form.append(key, String(value));
      }
      return form;
    }
    return undefined;
  }

  private async send(method: string, path: string, options: CallOptions = {}, idempotent = true): Promise<Response> {
    const headers: Record<string, string> = { ...this.headers };
    if (WRITE_METHODS.has(method) && idempotent) headers["Idempotency-Key"] = crypto.randomUUID();
    const auth = options.auth ?? "bearer";
    let refreshed = false;
    for (let attempt = 0; ; ) {
      const requestHeaders = { ...headers, ...(await this.authHeaders(auth)) };
      const body = this.body(options, requestHeaders);
      let response: Response;
      try {
        response = await this.fetchImpl(this.url(path, options.query), {
          method,
          headers: requestHeaders,
          body,
          signal: AbortSignal.timeout(this.options.timeoutMs ?? 30_000),
        });
      } catch (error) {
        if (attempt < this.maxRetries) {
          await this.sleep(this.backoff(attempt, null));
          attempt++;
          continue;
        }
        throw new APIConnectionError(`Couldn't reach OpenClear: ${String(error)}`, { cause: error });
      }
      if (response.status === 401 && auth === "bearer" && !this.token.fixed && !refreshed) {
        await response.body?.cancel();
        await this.bearer(true); // revoked or expired early: get a new one, once
        refreshed = true;
        continue;
      }
      const inFlight = response.status === 409 && attempt > 0 && "Idempotency-Key" in headers;
      if ((RETRY_STATUSES.has(response.status) || inFlight) && attempt < this.maxRetries) {
        await response.body?.cancel();
        await this.sleep(this.backoff(attempt, response.headers.get("retry-after")));
        attempt++;
        continue;
      }
      if (response.status >= 400) throw await this.error(response);
      return response;
    }
  }

  private backoff(attempt: number, retryAfter: string | null): number {
    const seconds = retryAfter === null ? NaN : Number(retryAfter);
    if (Number.isFinite(seconds)) return Math.min(seconds, 60) * 1000;
    return Math.min(500 * 2 ** attempt, 8000) * (0.5 + Math.random() / 2);
  }

  private async error(response: Response): Promise<OpenClearError> {
    const text = await response.text();
    let body: unknown = text;
    try {
      body = JSON.parse(text);
    } catch {
      // not JSON
    }
    const error = errorFor(response.status, body);
    if (error instanceof RateLimitError) {
      const after = Number(response.headers.get("retry-after"));
      error.retryAfter = Number.isFinite(after) ? after : undefined;
    }
    return error;
  }

  private async decode<T>(response: Response, raw: boolean | undefined): Promise<T> {
    if (raw) return new Uint8Array(await response.arrayBuffer()) as T;
    const text = await response.text();
    return (text ? JSON.parse(text) : undefined) as T;
  }

  // --- What the generated endpoints call -------------------------------------------------

  async call<T>(method: string, path: string, options: CallOptions = {}): Promise<T> {
    return this.decode<T>(await this.send(method, path, options), options.raw);
  }

  /** Like `call`, also resolving to the response (for its headers). */
  async callWithResponse<T>(method: string, path: string, options: CallOptions = {}): Promise<[T, Response]> {
    const response = await this.send(method, path, options);
    return [await this.decode<T>(response.clone(), options.raw), response];
  }

  async *paginate<T>(method: string, path: string, options: CallOptions = {}): AsyncIterable<T> {
    let response = await this.send(method, path, options);
    for (;;) {
      const rows = ((await response.json()) ?? []) as T[];
      yield* rows;
      const next = nextLink(response.headers.get("link"));
      if (!next) return;
      response = await this.send(method, next, { auth: options.auth });
    }
  }
}

// The generated API areas, assigned in the constructor.
export interface OpenClear extends Omit<GeneratedAreas, "decisionFeed"> {}

export function nextLink(header: string | null): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(",")) {
    const match = /<([^>]+)>\s*;\s*rel="?next"?/.exec(part);
    if (match) return match[1];
  }
  return undefined;
}
