// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

/**
 * Sending many items at once. `submit` splits a large list into batches, sends each to the
 * bulk endpoint, and when OpenClear answers a batch with 202 (too large to import during
 * the call) waits for its background import. Either way you get one result per item, in
 * the order you sent them.
 */
import type { OpenClear } from "./client.ts";
import { BulkUploadError } from "./errors.ts";
import type { BulkSubmitResponse, BulkUploadAccepted, BulkUploadKind, BulkUploadRead, BulkUploadRowRead } from "./generated.ts";

export type BulkKind = "issued_items" | "paid_items" | "ach_transactions";

export interface BulkRowResult {
  /** The item's position in what you sent. */
  index: number;
  success: boolean;
  id?: string | null;
  error?: string | null;
  /** For a paid item or ACH debit: how matching went (e.g. "matched", "exception"). */
  status?: string | null;
}

export interface BulkResult {
  total: number;
  succeeded: number;
  failed: number;
  results: BulkRowResult[];
  failures: BulkRowResult[];
  /** Batches imported in the background. */
  uploadIds: string[];
}

const FINISHED = new Set(["completed", "rejected", "failed"]);

export class Bulk {
  pollIntervalMs = 2000;
  private readonly c: OpenClear;

  constructor(client: OpenClear) {
    this.c = client;
  }

  private send(kind: BulkKind, batch: unknown[]): Promise<BulkSubmitResponse | BulkUploadAccepted> {
    const endpoints = {
      issued_items: (b: unknown[]) => this.c.issuedItems.createIssuedItemsBulk(b as never),
      paid_items: (b: unknown[]) => this.c.paidItems.createPaidItemsBulk(b as never),
      ach_transactions: (b: unknown[]) => this.c.achTransactions.createAchTransactionsBulk(b as never),
    };
    return endpoints[kind](batch);
  }

  /** Creates every item; one bad item fails only itself (see `failures`). */
  async submit(
    kind: BulkKind,
    items: Iterable<unknown>,
    options: { batchSize?: number; timeoutMs?: number } = {},
  ): Promise<BulkResult> {
    const batchSize = options.batchSize ?? 1000;
    const results: BulkRowResult[] = [];
    const uploadIds: string[] = [];
    let batch: unknown[] = [];
    let offset = 0;

    const flush = async () => {
      if (batch.length === 0) return;
      const answer = await this.send(kind, batch);
      let rows: BulkRowResult[];
      if ("upload_id" in answer) {
        const upload = await this.wait(answer.upload_id, options);
        uploadIds.push(answer.upload_id);
        rows = [];
        for await (const row of this.c.bulkUploads.iterGetBulkUploadResults(answer.upload_id)) {
          rows.push({ index: row.index, success: row.success, id: row.id, error: row.error });
        }
        if (upload.status !== "completed" && rows.length < batch.length) throw failure(upload);
      } else {
        rows = answer.results.map((r) => ({ index: r.index, success: r.success, id: r.id, error: r.error, status: r.status }));
      }
      for (const row of rows) results.push({ ...row, index: Number(row.index) + offset });
      offset += batch.length;
      batch = [];
    };

    for (const item of items) {
      batch.push(item);
      if (batch.length >= batchSize) await flush();
    }
    await flush();
    results.sort((a, b) => a.index - b.index);
    const failures = results.filter((r) => !r.success);
    return { total: results.length, succeeded: results.length - failures.length, failed: failures.length, results, failures, uploadIds };
  }

  /**
   * Uploads a file in any import format the bank can use (`bulkUploads.listImportFormats()`)
   * and, unless `wait: false`, waits for its import. Rejects with BulkUploadError if the file
   * was rejected or the import failed.
   */
  async uploadFile(
    kind: BulkUploadKind,
    format: string,
    file: Blob,
    options: { createMissingAccounts?: boolean; wait?: boolean; timeoutMs?: number } = {},
  ): Promise<BulkUploadRead> {
    const accepted = await this.c.bulkUploads.uploadFile({
      kind, format, file, create_missing_accounts: options.createMissingAccounts ?? false,
    });
    if (options.wait === false) return this.c.bulkUploads.getBulkUpload(accepted.upload_id);
    const upload = await this.wait(accepted.upload_id, options);
    if (upload.status !== "completed") throw failure(upload);
    return upload;
  }

  /** Polls an upload until it's completed, rejected or failed. */
  async wait(uploadId: string, options: { timeoutMs?: number } = {}): Promise<BulkUploadRead> {
    const deadline = Date.now() + (options.timeoutMs ?? 3_600_000);
    for (;;) {
      const upload = await this.c.bulkUploads.getBulkUpload(uploadId);
      if (FINISHED.has(upload.status)) return upload;
      if (Date.now() >= deadline) throw new BulkUploadError(`Upload ${uploadId} still ${upload.status} after the timeout.`);
      await this.c.sleep(this.pollIntervalMs);
    }
  }
}

function failure(upload: BulkUploadRead): BulkUploadError {
  const error = new BulkUploadError(`Upload ${upload.id} ${upload.status}: ${upload.error ?? "see its row results"}`);
  error.upload = upload as unknown as Record<string, unknown>;
  return error;
}

export type { BulkUploadRowRead };
