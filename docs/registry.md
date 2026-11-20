# Skill registry trust

A skill pack is guidance the agent may follow. A registry that can silently
change what it serves is a supply-chain path into the prompt. Registry entries
are therefore **checksum-pinned**, and an install verifies before the pack
lands in `~/.sidekick/skills/`.

## The pin

Each vendored entry in `REGISTRY` has a pin in `src/sk/registry_trust.py`:

```python
"superpowers": {
    "commit": "22f0b463e7a5e579904dd6211a520321035526f7",
    "sha256": "c6958228f5aa0539d1215333a8a7e8649bfcfeef508c6bf261ec47ad82a9d092",
    "files": "15",
}
```

- `commit` is the upstream HEAD git commit at pin time — git's content
  checksum over the whole tree.
- `sha256` is a deterministic manifest over the pack's `SKILL.md` files:
  paths and bytes hashed in sorted order. It catches a checkout that changed
  its skill text without changing the recorded commit, and is independent of
  git.
- `files` is the expected count, shown for a quick sanity check.

Pins were computed on 2026-11-20. They are reviewed data, **never auto-updated
on install**.

## Installing

```bash
sk skills-registry                 # shows which entries are pinned
sk skills-install superpowers      # clones to a temp dir, verifies, then moves
sk skills-install superpowers --force
```

The installer:

1. clones into a sibling temporary directory;
2. checks `git rev-parse HEAD` against the pinned commit;
3. recomputes the `SKILL.md` manifest hash and compares it;
4. only then moves the directory into `~/.sidekick/skills/<name>`;
5. on any mismatch, removes the temp directory and refuses — nothing partial
   lands, and the refusal names both expected and actual values.

No new runtime dependency: verification uses the git CLI the installer already
requires plus `hashlib`.

## Unpinned sources

A raw git URL, or a registry entry without a pin, is **not installed by
default**:

```bash
sk skills-install https://github.com/you/your-packs --allow-unpinned
```

`--allow-unpinned` is an explicit statement that the operator trusts that
source. The refusal without it names the flag. This is the same global-only,
operator-controlled rule used for egress, search keys and tool policy: a
cloned repository cannot add a registry entry or opt itself in.

## Refreshing a pin deliberately

```bash
git clone --depth 1 https://github.com/you/your-packs /tmp/pack
sk skills-pin /tmp/pack
```

The command prints `commit`, `sha256` and `files` as JSON. A maintainer copies
those values into `registry_trust.py` in a reviewable commit. Do not refresh a
pin just because an install failed — a mismatch means the upstream changed,
and that change should be read first.

## Tests

`tests/test_registry_trust.py` (11 cases) covers manifest determinism and
content sensitivity, commit/manifest matching and mismatch, temp-directory
cleanup on refusal, pinned install success, unpinned refusal and opt-in,
pin computation from a real git checkout, and the three CLI surfaces.
`tests/test_registry.py` and `tests/test_skills.py` keep their install-flow
coverage with pin verification stubbed, since that behavior is tested directly
in the new module.

## See also

- [Threat model](threat-model.md) — the supply-chain boundary this pin protects.
- [Team memory and shared skills](team.md) — repo-supplied skills and their
  separate trust gate.
