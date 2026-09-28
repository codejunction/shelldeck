# Contributing to shelldeck

Thanks for helping. Bug reports, fixes and focused features are all welcome.

## Setup

You need [uv](https://docs.astral.sh/uv/) and git. uv installs Python 3.12+ for you.

```sh
git clone https://github.com/codejunction/shelldeck
cd shelldeck
uv sync
SHELLDECK_HOME=.devhome/cfg uv run shelldeck --port 5466 serve   # dev server with its own data folder
```

On Windows PowerShell, set the variable with `$env:SHELLDECK_HOME = ".devhome/cfg"` first. Port 5466 keeps a dev server clear of your everyday one on 5455.

## Before you open a pull request

```sh
uv run ruff check
uv run pytest -q
```

- **Tests.** Add or update a test for any behaviour change. The suite includes a real terminal round trip (ConPTY on Windows, a POSIX pty on Linux), so run it on the OS you changed code for.
- **UI changes.** Check them in a real browser. The frontend is plain HTML, CSS and JavaScript in `shelldeck/static/`, with no build step.
- **Keep it small.** Prefer the standard library and the existing dependencies over new ones.
- **Docs.** Update `README.md` when user-visible behaviour changes, and add a line under "Unreleased" in `CHANGELOG.md`.
- **Commits.** Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:` and so on).

## Releasing (maintainers)

1. Bump `version` in `pyproject.toml` and move the "Unreleased" notes in `CHANGELOG.md` under the new version.
2. Commit, then tag and push: `git tag v0.0.2 && git push origin main v0.0.2`.
3. The Release workflow runs the tests, checks that the tag matches the version, publishes to PyPI and creates the GitHub release.
