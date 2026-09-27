import { useMemo, useState } from 'react';
import { CodeBlock, SectionHeading } from '../components/ui';
import { COMMANDS, COMMAND_GROUPS } from '../data/content';

export default function Commands() {
  const [q, setQ] = useState('');
  const [group, setGroup] = useState<string>('all');
  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return COMMANDS.filter(
      (c) =>
        (group === 'all' || c.group === group) &&
        (!needle || (c.cmd + ' ' + c.what).toLowerCase().includes(needle))
    );
  }, [q, group]);

  return (
    <div className="wrap" style={{ paddingTop: 36 }}>
      <SectionHeading
        prompt="$ sk --help --"
        title="Command reference"
        sub="One input, every surface. Fullscreen TUI and plain REPL share the same dispatcher — 30+ slash commands with fuzzy autocomplete."
      />
      <input
        className="search"
        placeholder="$ filter… try “voice”, “memory”, “mcp”, “auth”"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      <div className="tabs">
        <button className={group === 'all' ? 'on' : ''} onClick={() => setGroup('all')}>
          all ({COMMANDS.length})
        </button>
        {COMMAND_GROUPS.map((g) => (
          <button key={g} className={group === g ? 'on' : ''} onClick={() => setGroup(g)}>
            {g}
          </button>
        ))}
      </div>
      <div className="card" style={{ padding: 6 }}>
        <table className="cmd-table">
          <thead>
            <tr>
              <th>command</th>
              <th>what</th>
              <th>group</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.cmd}>
                <td className="cmd-name">{r.cmd}</td>
                <td className="cmd-what">{r.what}</td>
                <td className="cmd-what" style={{ whiteSpace: 'nowrap' }}>
                  {r.group}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <div style={{ padding: 18, color: 'var(--faint)', fontSize: 13 }}>no matches — try “sk”.</div>
        )}
      </div>
      <div style={{ marginTop: 16 }}>
        <CodeBlock code={`sk run "refactor auth" --yes --model smart --json   # single-shot, machine-readable\nsk run "audit deps" --read-only                 # research, zero writes\nsk run "watch CI" --bg       # detaches, returns job id, notifies on completion\nsk jobs -n 5`} />
      </div>
    </div>
  );
}
