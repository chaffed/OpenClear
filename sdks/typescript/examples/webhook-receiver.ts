// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Chaffed

// A minimal webhook receiver for Node 22.18+ (node webhook-receiver.ts): verifies each event,
// skips one it has already seen, and fetches the exception through the API.
import { createServer } from "node:http";

import { OpenClear, WebhookVerificationError, webhooks } from "../src/index.ts";

const env = process.env;
const oc = new OpenClear({ baseUrl: env.OPENCLEAR_URL!, clientId: env.OPENCLEAR_CLIENT_ID, clientSecret: env.OPENCLEAR_CLIENT_SECRET });
const seen = new Set<string>(); // use your database in production

createServer(async (req, res) => {
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(chunk as Buffer);
  const body = Buffer.concat(chunks);
  let event;
  try {
    event = await webhooks.verify(env.OPENCLEAR_WEBHOOK_SECRET!, req.headers as Record<string, string>, body);
  } catch (error) {
    res.writeHead(error instanceof WebhookVerificationError ? 400 : 500).end();
    return;
  }
  res.writeHead(204).end(); // answer quickly; OpenClear retries anything else
  const id = String(req.headers["webhook-id"]);
  if (seen.has(id)) return;
  seen.add(id);
  if (event.type === "exception.created") {
    const exception = await oc.exceptions.getException(String(event.data.exception_id));
    console.log("New exception", exception.id);
  }
}).listen(Number(env.PORT ?? 8080));
