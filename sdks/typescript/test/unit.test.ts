// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

// The SDK against a fake fetch, where failures can be staged.
import assert from "node:assert/strict";
import { test } from "node:test";

import { NotFoundError, OpenClear, ServerError, ValidationError, WebhookVerificationError, nextLink, webhooks } from "../src/index.ts";

type Staged = Response | Error;

function fake(staged: Staged[]) {
  const seen: Request[] = [];
  const fetchImpl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    seen.push(request);
    if (new URL(request.url).pathname === "/api/v1/oauth/token") {
      return Response.json({ access_token: `t${seen.length}`, expires_in: 1800 });
    }
    const next = staged.shift();
    if (!next) throw new Error("nothing staged");
    if (next instanceof Error) throw next;
    return next;
  }) as typeof fetch;
  const oc = new OpenClear({ baseUrl: "https://oc.example", clientId: "occ_x", clientSecret: "s", fetch: fetchImpl });
  const waits: number[] = [];
  oc.sleep = async (ms) => {
    waits.push(ms);
  };
  return { oc, seen, waits };
}

test("writes are retried with the same idempotency key, honouring Retry-After", async () => {
  const { oc, seen, waits } = fake([
    new TypeError("fetch failed"),
    new Response(null, { status: 503 }),
    new Response(null, { status: 429, headers: { "Retry-After": "7" } }),
    Response.json({ id: "1" }, { status: 201 }),
  ]);
  const created = await oc.stopPayments.createStopPayment({ account_id: "a", check_number: "1" } as never);
  assert.deepEqual(created, { id: "1" });
  const writes = seen.filter((r) => new URL(r.url).pathname !== "/api/v1/oauth/token");
  assert.equal(writes.length, 4);
  assert.equal(new Set(writes.map((r) => r.headers.get("idempotency-key"))).size, 1);
  assert.equal(waits[2], 7000);
});

test("retries give up, and a rejected token is renewed once", async () => {
  const failing = fake(Array.from({ length: 4 }, () => new Response(null, { status: 503 })));
  await assert.rejects(failing.oc.issuedItems.listIssuedItems(), ServerError);

  const { oc, seen } = fake([Response.json({ detail: "expired" }, { status: 401 }), Response.json([])]);
  assert.deepEqual(await oc.issuedItems.listIssuedItems(), []);
  assert.equal(seen.filter((r) => new URL(r.url).pathname === "/api/v1/oauth/token").length, 2);
});

test("errors are typed, with the server's detail", async () => {
  const { oc } = fake([
    Response.json({ detail: "Issued item not found" }, { status: 404 }),
    Response.json({ detail: [{ loc: ["body", "issue_date"], msg: "Input should be a valid date" }] }, { status: 422 }),
  ]);
  await assert.rejects(oc.issuedItems.getIssuedItem("x"), (e: unknown) => e instanceof NotFoundError && e.status === 404);
  await assert.rejects(oc.issuedItems.createIssuedItem({} as never), (e: unknown) =>
    e instanceof ValidationError && e.errors.length === 1 && e.message.includes("issue_date"));
});

test("a next link stays on the configured host", async () => {
  const { oc, seen } = fake([
    Response.json([{ n: 1 }], { headers: { Link: '<http://evil.example/api/v1/issued-items?offset=1&limit=1>; rel="next"' } }),
    Response.json([{ n: 2 }]),
  ]);
  const rows: unknown[] = [];
  for await (const row of oc.issuedItems.iterIssuedItems({ limit: 1 })) rows.push(row);
  assert.deepEqual(rows, [{ n: 1 }, { n: 2 }]);
  const last = new URL(seen[seen.length - 1].url);
  assert.equal(last.host, "oc.example");
  assert.equal(last.searchParams.get("offset"), "1");
  assert.equal(nextLink('<https://a/x?after=2>; rel="next"'), "https://a/x?after=2");
});

test("webhooks: what sign makes, verify accepts; tampering and replays are refused", async () => {
  const secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw";
  const body = JSON.stringify({ type: "exception.created", timestamp: "2026-10-08T00:00:00Z", data: { exception_id: "x" } });
  const now = Math.floor(Date.now() / 1000);
  const headers = { "webhook-id": "msg_1", "webhook-timestamp": String(now), "webhook-signature": await webhooks.sign(secret, "msg_1", now, body) };
  assert.equal((await webhooks.verify(secret, headers, body)).data.exception_id, "x");
  assert.equal((await webhooks.verify(secret, new Headers(headers), new TextEncoder().encode(body))).type, "exception.created");
  await assert.rejects(webhooks.verify(secret, headers, body.replace('"x"', '"y"')), WebhookVerificationError);
  await assert.rejects(webhooks.verify(secret, headers, body, { now: now + 3600 }), /too old/);
  await assert.rejects(webhooks.verify(secret, {}, body), /Missing/);
});
