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

## Branches and pull requests

`main` is protected: nobody pushes to it directly, including maintainers. Every change goes through a pull request:

1. Branch off `main` as `feature/<name>` for new features or `fix/<name>` for bug fixes, e.g. `feature/split-presets` or `fix/escape-in-dialogs`. CI rejects other branch names.
2. Push the branch and open a pull request into `main`.
3. All CI checks (lint, the test matrix and the installers) must pass, and review threads must be resolved.
4. The PR is squash-merged, so write its title as a Conventional Commit; it becomes the commit message on `main`.

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

1. On a `fix/release-0.0.2` branch, bump `version` in `pyproject.toml` and move the "Unreleased" notes in `CHANGELOG.md` under the new version. Merge it through a PR.
2. Tag the merged commit on `main` and push the tag: `git switch main && git pull && git tag v0.0.2 && git push origin v0.0.2`.
3. The Release workflow runs the tests, checks that the tag matches the version, publishes to PyPI and creates the GitHub release.

### Release candidates

To let people try a `feature/<name>` or `fix/<name>` branch before it merges, publish a release candidate from it:

1. GitHub: Actions > Release > Run workflow, pick the branch, and enter a version such as `0.0.5rc1`.
   Or from a terminal: `gh workflow run release.yml --ref feature/<name> -f version=0.0.5rc1`.
2. The workflow runs the tests, sets that version in the build only (nothing is committed), and publishes to PyPI. It makes no tag and no GitHub release.

The version must look like `x.y.zrcN` and be newer than the one in `pyproject.toml` (usually the next patch). PyPI never reuses a version, so use `rc2`, `rc3` and so on for later builds. A release candidate counts as a pre-release, so `uv tool install shelldeck` and `pip install shelldeck` keep installing the latest stable release.
