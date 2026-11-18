/** Transport tests: a tiny fake of `sk serve`, no network beyond loopback. */

import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as http from "node:http";
import type { AddressInfo } from "node:net";
import * as os from "node:os";
import * as path from "node:path";
import { test } from "node:test";

import {
  Settings,
  SidekickError,
  assertLoopback,
  expandHome,
  health,
  turn,
} from "../client";

const TOKEN = "test-token";

function tokenFile(content: string = TOKEN): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "sk-ide-"));
  const file = path.join(dir, "serve.token");
  fs.writeFileSync(file, content, { mode: 0o600 });
  return file;
}

interface FakeOptions {
  token?: string;
  answer?: string;
  error?: string;
  onTurn?: (body: Record<string, unknown>) => void;
}

async function fakeServer(options: FakeOptions = {}) {
  const server = http.createServer((req, res) => {
    if ((req.headers.authorization || "") !== `Bearer ${options.token || TOKEN}`) {
      res.writeHead(401, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "unauthorized" }));
      return;
    }
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      if (req.url === "/health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ ok: true, version: "9.9.9" }));
      } else if (req.url === "/turn") {
        options.onTurn?.(JSON.parse(raw || "{}") as Record<string, unknown>);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(
          JSON.stringify(
            options.error ? { error: options.error } : { answer: options.answer || "an answer" },
          ),
        );
      } else {
        res.writeHead(404, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ error: "not found" }));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  return {
    endpoint: `http://127.0.0.1:${address.port}`,
    close: () => new Promise<void>((resolve) => server.close(() => resolve())),
  };
}

function settings(endpoint: string, tokenPath: string): Settings {
  return { endpoint, tokenPath, session: "vscode", timeoutMs: 5000 };
}

test("health returns the daemon version", async (t) => {
  const server = await fakeServer();
  t.after(() => server.close());
  const result = await health(settings(server.endpoint, tokenFile()));
  assert.deepEqual(result, { ok: true, version: "9.9.9" });
});

test("a wrong token fails clearly as an auth error", async (t) => {
  const server = await fakeServer({ token: "the-real-token" });
  t.after(() => server.close());
  await assert.rejects(
    health(settings(server.endpoint, tokenFile("a-different-token"))),
    (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "auth");
      assert.match(err.message, /401/);
      assert.match(err.message, /sk serve/);
      return true;
    },
  );
});

test("a missing token file says to start sk serve", async () => {
  await assert.rejects(
    health(settings("http://127.0.0.1:9", "/nonexistent/serve.token")),
    (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "auth");
      assert.match(err.message, /run `sk serve` first/);
      return true;
    },
  );
});

test("an empty token file says to restart sk serve", async () => {
  await assert.rejects(
    health(settings("http://127.0.0.1:9", tokenFile("   \n"))),
    (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "auth");
      assert.match(err.message, /restart `sk serve`/);
      return true;
    },
  );
});

test("a non-loopback endpoint is refused before any request", () => {
  for (const endpoint of ["http://example.com:8787", "http://0.0.0.0:8787", "https://127.0.0.1:8787"]) {
    assert.throws(() => assertLoopback(endpoint), (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "config");
      return true;
    });
  }
  assert.doesNotThrow(() => assertLoopback("http://127.0.0.1:8787"));
  assert.doesNotThrow(() => assertLoopback("http://[::1]:8787"));
  assert.doesNotThrow(() => assertLoopback("http://localhost:8787"));
});

test("turn carries the prompt and session and returns the answer", async (t) => {
  let seen: Record<string, unknown> = {};
  const server = await fakeServer({ answer: "reviewed", onTurn: (body) => (seen = body) });
  t.after(() => server.close());
  const answer = await turn(settings(server.endpoint, tokenFile()), "Review this code");
  assert.equal(answer, "reviewed");
  assert.equal(seen.message, "Review this code");
  assert.equal(seen.session, "vscode");
});

test("a refused connection says to start sk serve", async () => {
  await assert.rejects(
    health(settings("http://127.0.0.1:9", tokenFile())),
    (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "connection");
      assert.match(err.message, /Is `sk serve` running/);
      return true;
    },
  );
});

test("an agent error from the daemon is surfaced", async (t) => {
  const server = await fakeServer({ error: "model unavailable" });
  t.after(() => server.close());
  await assert.rejects(
    turn(settings(server.endpoint, tokenFile()), "Explain this"),
    (error: unknown) => {
      const err = error as SidekickError;
      assert.equal(err.kind, "api");
      assert.match(err.message, /model unavailable/);
      return true;
    },
  );
});

test("expandHome handles ~ and ~/ without a shell", () => {
  assert.equal(expandHome("~"), os.homedir());
  assert.equal(expandHome("~/x/y"), path.join(os.homedir(), "x/y"));
  assert.equal(expandHome("/tmp/x"), "/tmp/x");
});
