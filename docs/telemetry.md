# Crash telemetry (opt-in)

Sidekick sends **no telemetry by default**. No file is created and no request is
made unless you explicitly opt in. This page states exactly what is collected
and where it goes.

## Opting in

```bash
export SIDEKICK_TELEMETRY=1          # environment, wins over the file
```

Or in `~/.sidekick/config.toml`:

```toml
telemetry = true
```

Only the **global** config counts. A `.sidekick.toml` in a repository cannot
enable telemetry or set a collector — the same rule as keys, egress and search
providers.

## What is collected

One JSON line per uncaught crash:

```json
{
  "message": "secret <redacted-key> <path>",
  "os": "Linux",
  "python": "3.12.3",
  "schema": 1,
  "surface": "cli",
  "type": "RuntimeError",
  "version": "0.41.0"
}
```

| Field | Contents |
|---|---|
| `schema` | Payload version (currently 1). |
| `type` | The exception class name only. |
| `message` | The exception message after redaction, capped at 500 characters. |
| `version` | Sidekick's semantic version. |
| `python` | Python version, just the number. |
| `os` | `platform.system()` — e.g. `Linux`, `Darwin`. |
| `surface` | `cli` for the command/TUI entry point; capped at 40 characters. |

**Never collected:** prompts, file contents, absolute paths (replaced by
`<path>`), URLs (`<url>`), email addresses, API keys, tokens, environment
values, tool arguments, stack locals or tracebacks. Redaction strips
`sk-or-…` keys, `Bearer …`, `api_key=`/`token=`/`password=`/`secret=` values,
emails, URLs and absolute paths before the message is stored.

## Where it goes

**Local by default.** The line is appended to `~/.sidekick/telemetry.jsonl`
with owner-only permissions. Nothing leaves the machine.

**Remote only if you name a collector.** Setting either of these adds a POST
with the same JSON body:

```bash
export SIDEKICK_TELEMETRY_ENDPOINT=https://your-collector.example/in
```

```toml
telemetry_endpoint = "https://your-collector.example/in"
```

The opt-in flag alone never selects a remote endpoint. The request has a
three-second timeout and its failures are swallowed.

## Failure behaviour

Reporting is best-effort and cannot change what the user sees. If the sink is
unwritable or the collector is down, the original traceback still prints and
the exit code is unchanged. Turning telemetry on never turns an error into a
silent one.

The hook is installed by the `sk` console-script entry point and by
`python -m sk`. It chains to the previous `sys.excepthook`, so the normal
traceback is preserved.

## Tests

`tests/test_telemetry.py` covers: off by default with no file; environment and
global-config opt-in; a project file that cannot enable it; redaction of keys,
tokens, emails, URLs and paths; a payload with no traceback/locals; a remote
request only with an explicit endpoint; swallowed sink/network failures; and
the excepthook delegating while reporting only when enabled.

## See also

- [Threat model](threat-model.md) — the trust boundary this data crosses when
  you opt in.
- [The SDK and the local API](sdk.md) — surfaces that also run through the CLI
  entry point.
