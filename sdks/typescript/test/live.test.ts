// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

// The SDK against a running OpenClear. tests/test_sdk/test_typescript_sdk.py starts one,
// sets up a bank and an API client, and passes them in OPENCLEAR_TEST_* variables.
import assert from "node:assert/strict";
import { test } from "node:test";

import { AuthenticationError, NotFoundError, OpenClear, PermissionDeniedError, ValidationError, webhooks } from "../src/index.ts";

const env = process.env;
const live = Boolean(env.OPENCLEAR_TEST_URL);
const skip = live ? false : "needs OPENCLEAR_TEST_URL (run tests/test_sdk/test_typescript_sdk.py)";

function client(): OpenClear {
  const oc = new OpenClear({ baseUrl: env.OPENCLEAR_TEST_URL!, clientId: env.OPENCLEAR_TEST_CLIENT_ID, clientSecret: env.OPENCLEAR_TEST_CLIENT_SECRET });
  oc.sleep = async () => {};
  oc.bulk.pollIntervalMs = 50;
  return oc;
}

function item(number: string) {
  return { account_id: env.OPENCLEAR_TEST_ACCOUNT_ID!, check_number: number, amount: "10.00", payee_name: "Vendor", issue_date: "2026-03-01" };
}

test("sync, create, and page through", { skip }, async () => {
  const oc = client();
  const customer = await oc.customers.upsertCustomer("CIF-TS", { name: "Acme Corp" });
  const account = await oc.accounts.upsertAccount("TS-ACCT-1", { name: "Operating", customer_number: "CIF-TS" });
  assert.equal(account.customer_id, customer.id);
  for (let n = 0; n < 5; n++) await oc.issuedItems.createIssuedItem(item(`TS${n}`));
  assert.equal((await oc.issuedItems.listIssuedItems({ limit: 2 })).length, 2);
  const numbers: string[] = [];
  for await (const row of oc.issuedItems.iterIssuedItems({ limit: 2 })) numbers.push(row.check_number);
  for (let n = 0; n < 5; n++) assert.ok(numbers.includes(`TS${n}`));
});

test("errors are typed", { skip }, async () => {
  const oc = client();
  await assert.rejects(oc.issuedItems.getIssuedItem(crypto.randomUUID()), NotFoundError);
  await assert.rejects(oc.issuedItems.createIssuedItem({ ...item("BAD"), issue_date: "not a date" }), ValidationError);
  const viewer = new OpenClear({ baseUrl: env.OPENCLEAR_TEST_URL!, clientId: env.OPENCLEAR_TEST_VIEWER_ID, clientSecret: env.OPENCLEAR_TEST_VIEWER_SECRET });
  await assert.rejects(viewer.issuedItems.createIssuedItem(item("NO")), PermissionDeniedError);
  const wrong = new OpenClear({ baseUrl: env.OPENCLEAR_TEST_URL!, clientId: env.OPENCLEAR_TEST_CLIENT_ID, clientSecret: "wrong" });
  await assert.rejects(wrong.issuedItems.listIssuedItems(), AuthenticationError);
});

test("bulk waits for background batches and keeps the order", { skip }, async () => {
  const oc = client();
  const items = Array.from({ length: 5 }, (_, n) => item(`TB${n}`));
  items[3] = { ...items[3], account_id: crypto.randomUUID() };
  const result = await oc.bulk.submit("issued_items", items, { batchSize: 3 }); // the server imports more than 2 in the background
  assert.deepEqual([result.total, result.succeeded, result.failed], [5, 4, 1]);
  assert.deepEqual(result.results.map((r) => r.index), [0, 1, 2, 3, 4]);
  assert.equal(result.failures[0].index, 3);
  assert.equal(result.uploadIds.length, 1);
});

test("upload a file", { skip }, async () => {
  const oc = client();
  const csv = `account_number,check_number,presented_amount,presented_date\n${env.OPENCLEAR_TEST_ACCOUNT_NUMBER},TF1,12.50,2026-03-02\n`;
  const upload = await oc.bulk.uploadFile("paid_items", "tabular", new File([csv], "presented.csv", { type: "text/csv" }));
  assert.equal(upload.status, "completed");
  assert.equal(upload.succeeded, 1);
});

test("the decision feed is consumed once and acknowledged", { skip }, async () => {
  const oc = client();
  const handled: number[] = [];
  const last = await oc.decisionFeed.consume("ts-core", (d) => {
    handled.push(Number(d.sequence));
  }, { pageSize: 2 });
  assert.deepEqual(handled, [1, 2, 3]);
  assert.equal(last, 3);
  assert.equal(await oc.decisionFeed.position("ts-core"), 3);
  assert.equal(await oc.decisionFeed.consume("ts-core", () => assert.fail("nothing new")), 3);
  const file = await oc.decisionFeed.decisionFile({ layout: "openclear_csv" });
  assert.ok(file instanceof Uint8Array);
  assert.ok(new TextDecoder().decode(file).startsWith("sequence,"));
});

test("acting for a person", { skip }, async () => {
  const created = await client().actingAs(env.OPENCLEAR_TEST_PERSON!).issuedItems.createIssuedItem(item("TA1"));
  assert.equal(created.check_number, "TA1");
});

test("verifies a webhook the server signed", { skip }, async () => {
  const event = await webhooks.verify(env.OPENCLEAR_TEST_WEBHOOK_SECRET!, JSON.parse(env.OPENCLEAR_TEST_WEBHOOK_HEADERS!), env.OPENCLEAR_TEST_WEBHOOK_BODY!);
  assert.equal(event.type, "exception.created");
});
