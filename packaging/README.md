# Distribution notes

`sidekick-agent` is published to PyPI when code is **merged to `main`** of the
canonical repository — **`Faisal-Fayaz/sidekick`** — and nothing else. Every
other channel builds from the PyPI release.

## PyPI (primary)

```bash
make build && make check      # local: builds sdist+wheel + twine check
make publish                  # manual PyPI upload (needs ~/.pypirc / TWINE creds)
make publish-test             # → test.pypi.org
```

Automated publishing is credential-free and merge-driven
(`.github/workflows/release.yml`):

1. In the PR that will become a release, bump `__version__` in
   `src/sk/__init__.py` (single source of truth).
2. Merge the PR into `main` → the release workflow runs **only** on
   `Faisal-Fayaz/sidekick`, publishes the sdist+wheel to PyPI via [trusted publishing],
   and creates a `v<version>` GitHub Release with the artifacts.
3. The workflow is gated on `github.repository == 'Faisal-Fayaz/sidekick'`, so
   merges/pushes in **forks can never publish** — and if it somehow ran there,
   the OIDC token's `repo_owner` would fail PyPI's trusted-publisher check.
4. If version `__version__` is already on PyPI, the workflow exits silently
   (non-release merges are no-ops).

### Dry run and recovery

**Dry run.** *Actions → release → Run workflow*, set `target` to `testpypi`
(default) or `pypi`. This is a genuine dry run: it builds and uploads through
trusted publishing to <https://test.pypi.org/project/sidekick-agent/> under its
own `testpypi` environment.

**Recovery — PyPI has the release but the repo has no tag.** A previous run can
upload to PyPI and then fail before the "Create tag" step, leaving PyPI ahead of
the git tags. Re-running used to be useless: the workflow saw the version on
PyPI, set `publish=false`, skipped the `publish` job, and the `github-release`
job required that job to have *succeeded* — so it was skipped forever. The
condition now also accepts "already on PyPI", and the `reconcile` dispatch
input makes it reachable by hand:

1. *Actions → release → Run workflow*
2. `target` = `pypi`, `reconcile` = `true`

That creates the missing `v<version>` tag and GitHub Release. It does **not**
re-upload to PyPI. There is no need to push a tag by hand — `on.push` filters
on `branches: [main]`, so a bare tag push runs no workflow at all.

### Version numbers are not recoverable

A version number is burned once written into `src/sk/__init__.py`, whether or
not it is ever published: PyPI refuses to republish a number, and git tags
cannot be reused. `v0.14.0` and `v0.28.0` were both burned this way, which is
why the sequence in `tests/test_release_integrity.py` lists them as known gaps.
A *new* gap fails that test. Bump deliberately, and check the bump survives to
a tag.

### One-time trusted-publisher setup (PyPI account side)

On the PyPI account that will own `sidekick-agent`, register a publishing
source at <https://pypi.org/manage/account/publishing/>:

- **Owner** `Faisal-Fayaz` · **Repository** `sidekick` · **Workflow** `release.yml`
- **Environment** `pypi` · **Project** `sidekick-agent`

To make the dry run above work, register a second publishing source on
<https://test.pypi.org/manage/account/publishing/> — owner `Faisal-Fayaz`, repo
`sidekick`, workflow `release.yml`, environment `testpypi`. **Until that exists,
the dry run fails at the upload step** with `invalid-publisher`, after build,
`twine check` and the release-sequence guard have all already passed. That is a
useful dry run for everything except the upload itself, but it is not a full
green light — don't read a red X as "the release is broken".

> The "Owner" must be the GitHub user who **owns the repo the workflow runs in**
> (Faisal-Fayaz), not the person who pushes or registers. It matches the
> `repo_owner` claim GitHub puts in the OIDC token.

Install endpoints (see root README):
`uv tool install sidekick-agent` · `pipx install sidekick-agent` · `pip install sidekick-agent` · `pip install sidekick-agent[voice]`

## AUR (Arch Linux)

`packaging/aur/PKGBUILD` — upload to <https://aur.archlinux.org> to get
`python-sidekick-agent`. Before uploading: set a real `sha256sums` via
`updpkgsums` and `makepkg` locally to verify.

## conda-forge

conda-forge packages live in a separate **feedstock** repo (reciped via bot),
not in this project. To enable `conda install sidekick-agent`:

1. Follow <https://conda-forge.org/docs/maintainer/adding_pkgs.html>.
2. The feedstock builds `python -m pip install .` from the PyPI sdist, so no
   in-repo changes are required — `pyproject.toml` is already PEP 517/hatchling.

[trusted publishing]: https://docs.pypi.org/trusted-publishers/