// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/** What the generated endpoint classes call; OpenClear is the one implementation. */
export interface CallOptions {
  query?: Record<string, unknown>;
  json?: unknown;
  /** multipart/form-data: Blobs are sent as files, everything else as text. */
  multipart?: Record<string, unknown>;
  /** application/x-www-form-urlencoded. */
  form?: Record<string, unknown>;
  auth?: "bearer" | "platform" | "none";
  /** Return the body's bytes (a file) instead of parsing JSON. */
  raw?: boolean;
}

export interface Transport {
  call<T>(method: string, path: string, options?: CallOptions): Promise<T>;
  paginate<T>(method: string, path: string, options?: CallOptions): AsyncIterable<T>;
}
