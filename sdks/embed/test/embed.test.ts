// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed
import assert from "node:assert/strict";
import { test } from "node:test";

import { readMessage } from "../src/index.ts";

const frameWindow = {};
const expected = { origin: "https://openclear.bank.example", window: frameWindow };

test("only OpenClear's frame, from its origin, is listened to", () => {
  const good = { origin: expected.origin, source: frameWindow, data: { source: "openclear", type: "resize", height: 812 } };
  assert.deepEqual(readMessage(good, expected), { type: "resize", height: 812 });
  assert.equal(readMessage({ ...good, origin: "https://evil.example" }, expected), null);
  assert.equal(readMessage({ ...good, source: {} }, expected), null);
  assert.equal(readMessage({ ...good, data: { type: "resize" } }, expected), null);
  assert.equal(readMessage({ ...good, data: { source: "openclear", type: "steal-cookies" } }, expected), null);
});

test("event details are checked and bounded", () => {
  const id = "0b7c9e3e-6a55-4bb2-9e2f-0a1b2c3d4e5f";
  const decided = readMessage({ origin: expected.origin, source: frameWindow, data: { source: "openclear", type: "exception-decided", exceptionId: id } }, expected);
  assert.deepEqual(decided, { type: "exception-decided", exceptionId: id });
  const odd = readMessage({ origin: expected.origin, source: frameWindow, data: {
    source: "openclear", type: "navigated", path: "/ui/x".padEnd(900, "x"), exceptionId: "<script>", height: 1e9 } }, expected);
  assert.equal(odd?.path?.length, 500);
  assert.equal(odd?.exceptionId, undefined);
  assert.equal(odd?.height, 100_000);
});
