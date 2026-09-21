# Distribution notes

`sidekick-agent` is published to PyPI when code is **merged to `main`** of the
canonical repository — **`Faisal01011/sidekick`** — and nothing else. Every
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
   `Faisal01011/sidekick`, publishes the sdist+wheel to PyPI via [trusted publishing],
   and creates a `v<version>` GitHub Release with the artifacts.
3. The workflow is gated on `github.repository == 'Faisal01011/sidekick'`, so
   merges/pushes in **forks can never publish** — and if it somehow ran there,
   the OIDC token's `repo_owner` would fail PyPI's trusted-publisher check.
4. If version `__version__` is already on PyPI, the workflow exits silently
   (non-release merges are no-ops).

### One-time trusted-publisher setup (PyPI account side)

On the PyPI account that will own `sidekick-agent`, register a publishing
source at <https://pypi.org/manage/account/publishing/>:

- **Owner** `Faisal01011` · **Repository** `sidekick` · **Workflow** `release.yml`
- **Environment** `pypi` · **Project** `sidekick-agent`

Same on <https://test.pypi.org/manage/account/publishing/> for the `testpypi`
environment (optional, for manual test runs).

> The "Owner" must be the GitHub user who **owns the repo the workflow runs in**
> (Faisal01011), not the person who pushes or registers. It matches the
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