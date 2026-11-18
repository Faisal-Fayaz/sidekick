/** The whole transport: loopback-only, token-authenticated, no model calls.
 *
 * This module deliberately has no `vscode` import, so it can be tested with
 * Node's built-in test runner against a tiny fake of `sk serve`. It is a client
 * over the local API, nothing more: the daemon owns the agent, the tools, the
 * approval gate and the audit ledger.
 */

import * as fs from "node:fs";
import * as http from "node:http";
import * as os from "node:os";
import * as path from "node:path";

export interface Settings {
  endpoint: string;
  tokenPath: string;
  session: string;
  timeoutMs: number;
}

export type ErrorKind = "config" | "auth" | "connection" | "api";

export class SidekickError extends Error {
  readonly kind: ErrorKind;
  constructor(kind: ErrorKind, message: string) {
    super(message);
    this.name = "SidekickError";
    this.kind = kind;
  }
}

/** Expand a leading `~` without shelling out. */
export function expandHome(value: string): string {
  const text = (value || "").trim();
  if (text === "~") return os.homedir();
  if (text.startsWith("~/")) return path.join(os.homedir(), text.slice(2));
  return text;
}

/** Refuse anything that is not loopback. Nothing may leave this machine. */
export function assertLoopback(endpoint: string): void {
  let url: URL;
  try {
    url = new URL(endpoint);
  } catch {
    throw new SidekickError("config", `invalid sidekick.endpoint: ${endpoint}`);
  }
  if (url.protocol !== "http:") {
    throw new SidekickError("config", `sidekick.endpoint must be http (got ${url.protocol})`);
  }
  const host = url.hostname.replace(/^\[|\]$/g, "").toLowerCase();
  if (host !== "localhost" && host !== "::1" && !/^127\./.test(host)) {
    throw new SidekickError(
      "config",
      `refusing non-loopback endpoint ${host!}: Sidekick talks to this machine only`,
    );
  }
}

/** Read the daemon's token. A missing or empty token is a clear error. */
export function readToken(tokenPath: string): string {
  const file = expandHome(tokenPath);
  let token: string;
  try {
    token = fs.readFileSync(file, "utf8").trim();
  } catch {
    throw new SidekickError("auth", `Sidekick token not found at ${file}; run \`sk serve\` first`);
  }
  if (!token) {
    throw new SidekickError("auth", `Sidekick token at ${file} is empty; restart \`sk serve\``);
  }
  return token;
}

function request(
  settings: Settings,
  method: "GET" | "POST",
  route: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  assertLoopback(settings.endpoint);
  const token = readToken(settings.tokenPath);
  const url = new URL(settings.endpoint);
  const payload = body === undefined ? null : JSON.stringify(body);

  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        hostname: url.hostname.replace(/^\[|\]$/g, ""),
        port: url.port || 80,
        path: route,
        method,
        headers: {
          Authorization: `Bearer ${token}`,
          ...(payload === null
            ? {}
            : {
                "Content-Type": "application/json",
                "Content-Length": Buffer.byteLength(payload),
              }),
        },
      },
      (res) => {
        const chunks: Buffer[] = [];
        res.on("data", (chunk: Buffer) => chunks.push(chunk));
        res.on("end", () => {
          const raw = Buffer.concat(chunks).toString("utf8");
          let data: Record<string, unknown> = {};
          try {
            const parsed: unknown = JSON.parse(raw);
            if (parsed && typeof parsed === "object") data = parsed as Record<string, unknown>;
          } catch {
            // A non-JSON error body is still reported with its status.
          }
          const code = res.statusCode ?? 0;
          if (code === 401) {
            reject(
              new SidekickError(
                "auth",
                `Sidekick rejected the token (401). Check ${settings.tokenPath} matches the running \`sk serve\`.`,
              ),
            );
            return;
          }
          if (code < 200 || code >= 300) {
            const reason = String(data.error || raw || `HTTP ${code}`);
            reject(new SidekickError("api", `Sidekick returned ${code}: ${reason}`));
            return;
          }
          resolve(data);
        });
      },
    );

    req.setTimeout(Math.max(1000, settings.timeoutMs), () => {
      req.destroy(new Error("timed out"));
    });
    req.on("error", (err: NodeJS.ErrnoException) => {
      if (err.code === "ECONNREFUSED" || err.code === "ENOTFOUND" || err.message === "timed out") {
        reject(
          new SidekickError(
            "connection",
            `Cannot reach Sidekick at ${settings.endpoint}. Is \`sk serve\` running?`,
          ),
        );
        return;
      }
      reject(new SidekickError("connection", `Sidekick connection failed: ${err.message}`));
    });
    if (payload !== null) req.write(payload);
    req.end();
  });
}

/** GET /health — a connectivity and version check, no agent call. */
export async function health(settings: Settings): Promise<{ ok: boolean; version: string }> {
  const data = await request(settings, "GET", "/health");
  return { ok: data.ok === true, version: String(data.version || "") };
}

/** POST /turn — the daemon runs the agent; this client only carries text. */
export async function turn(settings: Settings, message: string): Promise<string> {
  const data = await request(settings, "POST", "/turn", {
    message,
    session: settings.session,
  });
  if (typeof data.error === "string" && data.error) {
    throw new SidekickError("api", data.error);
  }
  return String(data.answer || "");
}
