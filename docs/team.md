# Team memory and shared skill packs

Sidekick's memory and skills are personal by default. The team layer lets a
group share them without letting a cloned repository grant itself powers. The
same rules apply everywhere in Sidekick: the user's own files always load, a
project's files load only when the operator opted in, and permissions are
stated in data — never inferred from prose inside a skill.

## The two layers

| Layer | Location | Loaded when | Authority |
|---|---|---|---|
| User | `~/.sidekick/team/` | Always | Operator-authored: trusted facts and guidance |
| Project | `.sidekick/team/`, `.sidekick/skills/` | Only with `SIDEKICK_TRUST_REPO=1` | Data from a cloned repo: fenced as untrusted |

The trust flag is read from **your outer shell environment**, never from a file
in the repository, so a project cannot self-authorize. It is the same gate used
by custom commands, review packs and checks (#287), exposed as
`custom_commands.is_project_trusted()`.

```bash
# in your shell — never commit this to the repo
export SIDEKICK_TRUST_REPO=1
sk team check
```

## Example repository layout

A repository that wants to share team data looks like this:

```text
my-app/
├── .sidekick/
│   ├── team/
│   │   ├── memories.md          # project facts, one "- ..." line each
│   │   └── policy.toml          # IGNORED — a repo cannot set policy
│   └── skills/
│       ├── deploy.md            # shared skill pack (guidance)
│       └── oncall/SKILL.md      # nested packs are not loaded; use *.md at the root
├── README.md
└── src/
```

And the operator's own machine:

```text
~/
└── .sidekick/
    └── team/
        ├── memories.md          # user facts, one "- ..." line each
        ├── policy.toml          # the ONLY policy file that is read
        └── skills/
            └── house-style.md   # user skill packs (always loaded)
```

### `memories.md`

Only lines starting with `- ` are read; headings, prose and comments are
ignored. Entries longer than 2,000 characters are skipped.

```markdown
# Things our team knows

- deploys happen on Fridays after the 14:00 freeze
- staging is at staging.internal, never poke production from a shell
```

User memories are shown in `SAVED MEMORIES` with a `[team/user]` label. Project
memories are **never** treated as facts: they appear only inside the untrusted
fence, labelled with their source, alongside other outside-content such as repo
documentation and web pages.

### `policy.toml`

The user's `~/.sidekick/team/policy.toml` is the only place a tool allow-list
for shared skills can be set:

```toml
schema = 1
allow_tools = ["read_file", "search_code"]
```

A missing, malformed or wrong-schema policy means **no tools are allowed** —
failure closes. A policy file inside the project is ignored entirely.

### Shared skill packs

Skill packs are Markdown with the same frontmatter as the skill index:

```markdown
---
name: deploy
description: How this team ships
tools: read_file
---

Read the runbook first. Announce the deploy window in #deploys before you start.
```

`tools` (or `allowed_tools`) declares what the pack expects. A **user** pack may
declare any tool — the operator owns the policy. A **project** pack may declare
only tools the user's policy already allows. Asking for anything else refuses
the whole pack, with a reason printed by `sk team check`. That is the "a project
layer cannot widen permissions" rule: a repository can suggest, but it cannot
grant.

A shared skill is guidance, not executable code. The loader never runs a
command found inside a pack, and it does not expand shell or file references
the way custom commands do. When the agent acts on a pack, the ordinary approval
gate and audit ledger govern the action exactly as they would for any other
tool call.

## Inspecting the result

```bash
sk team check          # human-readable trust state, sources, skills, refusals
sk team check --json   # machine-readable report
```

`/memories` and `sk memories` both list team facts with their `[team/user]` or
`[team/project]` source, so the origin is always visible.

## What this does not do

- It does not run commands from a pack, and it does not bypass approval.
- It does not let a project set policy, add tools, or mark its own facts trusted.
- It is not a sync service or a seat manager — it is files on disk, shared by
  whatever mechanism your team already uses (git, a dotfiles repo, a drive).
- The `skills` tool still loads the personal skill index from `~/.sidekick/skills`;
  team packs are injected as labelled context instead.
