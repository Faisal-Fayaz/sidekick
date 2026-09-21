# Distribution notes

`sidekick-agent` is first published to **PyPI**; every other channel builds from that.

## PyPI (primary)

Built with `uv build` (hatchling backend) → sdist + wheel in `dist/`.

```bash
make build && make check      # local: builds + twine check
make publish                  # needs ~/.pypirc or TWINE creds (not stored here)
make publish-test             # → test.pypi.org
```

Publishing to PyPI is a zero-token, tag-driven **GitHub Actions** workflow
(`.github/workflows/release.yml`) using [trusted publishing].

1. Bump `__version__` in `src/sk/__init__.py` (single source of truth).
2. `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
3. One-time setup (any machine — done once on the account that owns PyPI):
   - `https://pypi.org/manage/account/publishing/` → add publisher for
     **Irfanwani/sidekick**, workflow `release.yml`, environment `pypi`.
   - Same on `test.pypi.org` for the `testpypi` environment.

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