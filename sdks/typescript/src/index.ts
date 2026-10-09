// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/**
 * The OpenClear SDK for TypeScript and JavaScript: a typed client for OpenClear's API
 * (positive pay and ACH exception decisions), for banking cores, online banking providers
 * and a bank's own systems. See README.md.
 */
export { OpenClear, DecisionFeedApi, nextLink } from "./client.ts";
export type { OpenClearOptions } from "./client.ts";
export { Bulk } from "./bulk.ts";
export type { BulkKind, BulkResult, BulkRowResult } from "./bulk.ts";
export * from "./errors.ts";
export * as webhooks from "./webhooks.ts";
export type { WebhookEvent } from "./webhooks.ts";
export * from "./generated.ts";
export type { CallOptions, Transport } from "./transport.ts";
