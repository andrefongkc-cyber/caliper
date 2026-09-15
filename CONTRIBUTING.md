# Contributing

Read [docs/architecture.md](docs/architecture.md) first. It explains how the pieces fit, and
most rules below follow from it.

## Setup

```bash
uv sync                                  # engine + dev tools
uv sync --extra app --group app-test     # shell work: adds PySide6 and pytest-qt
```

Before pushing, run what CI runs:

```bash
uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest
```

## Two streams, one repository

V1 is built by two people in parallel. Each works in their own git worktree:

```bash
git worktree add ../caliper-core  -b stream/core
git worktree add ../caliper-shell -b stream/shell
```

| Stream | Owns | Branch names |
|---|---|---|
| A: Core | `caliper/engine/`, `bench/`, `tests/` (except `tests/app/`), `docs/workplan/core.md` | `stream/core`, `stream/core/<topic>` |
| B: Shell | `caliper/app/`, `tests/app/`, `docs/workplan/shell.md` | `stream/shell`, `stream/shell/<topic>` |

Either stream may add ADRs under `docs/adr/`.

To start an agent session for a stream, paste [docs/kickoff/stream-a-core.md](docs/kickoff/stream-a-core.md)
or [docs/kickoff/stream-b-shell.md](docs/kickoff/stream-b-shell.md) into a fresh Claude Code session in that worktree.

**Other branches:**

| Branch | For | Restrictions |
|---|---|---|
| `contracts/<topic>` | Changing `caliper/contracts/` | Pauses the other stream; both people review |
| `shared/<topic>` | `pyproject.toml`, `uv.lock`, `CLAUDE.md`, `WORKPLAN.md`, `.github/`, docs | None |

The **`boundaries`** CI check fails a pull request from a stream branch that touches files
outside its area. It also fails branches that don't follow these names. To run it locally:

```bash
python3 .github/scripts/check_boundaries.py --branch stream/core --base main --head HEAD
```

### Changing the contract

`caliper/contracts/` is the seam both streams build against. After the Phase 0.5 spike,
`commands.py`, `document.py`, `queries.py`, and `errors.py` are frozen; `kernel.py` stays
Provisional. To change a frozen file:

1. Stop, and write down exactly what you need and why.
2. Agree on it with the other stream, which pauses work that depends on the change.
3. Make the change on a `contracts/<topic>` branch, including any adaptation needed on both
   sides, and get both people's review.

Additive changes are cheaper but still go through this process: a new error code, enum
member, or entity field with a default.

## Commits and pull requests

- **Commits:** small and single-concern, using [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(engine): add rectangle command`, `fix(app): snap to grid at high zoom`,
  `docs(adr): ...`). Scopes: `contracts`, `engine`, `app`, `ai`, `bench`, `ci`, `build`,
  `docs`.
- **Rebase onto `main` before every merge,** not once at the end, so conflicts show up
  small.
- **Reviews:** every PR needs an approving review from the other person; GitHub requests
  them automatically through [`.github/CODEOWNERS`](.github/CODEOWNERS).
- **PR checklist:**
  - [ ] CI green (lint, format, types, tests, boundaries)
  - [ ] Your stream's workplan updated
  - [ ] ADR written if this makes an architectural decision
  - [ ] New dependency? License stated (ADR 0006)
  - [ ] No secrets, no `.env`

## Workplans

- **Your stream's file** (`docs/workplan/core.md` or `shell.md`) is updated after every
  meaningful change, not only when a task finishes. Anyone opening it cold should be able to
  tell what's done, what's in flight, and what's next.
- **First line:** `Status: doing X, next: Y`.
- **Markers:** `[ ]` not started, `[~]` in progress, `[x]` done.
- **`WORKPLAN.md` at the root** is a short, human-edited index.

## Architecture decision records

Write an ADR when a decision would be expensive to reverse or would surprise someone later.

- **Location and naming:** `docs/adr/NNNN-short-title.md`, next free number.
- **Sections:** Status, Date, Context, Decision, Consequences, Alternatives considered.
- **Immutable once Accepted.** To change a decision, write a new ADR that supersedes the old
  one and mark the old one "Superseded by NNNN". A Proposed ADR may be edited until it is
  accepted.
- **Indexed** in `CLAUDE.md`.

## Dependencies and secrets

- **Adding a dependency:** use `uv add` on a `shared/` branch. No GPL or AGPL, ever
  (ADR 0006); CI checks installed licenses.
- **Secrets:** never commit `.env`, API keys, or tokens. Document new environment
  variables in `.env.example`.

## Repository settings (maintainers)

The repository is public, which makes branch protection and macOS CI runners free.

**Settings:**

- **Require a pull request before merging:**
  - 1 approval
  - Require review from Code Owners
  - Dismiss stale approvals when new commits are pushed
- **Require status checks to pass:**
  - `core (Linux)`, `app (macOS)`, and `boundaries`
  - Require branches to be up to date
- **Require linear history** (matches the rebase workflow).
- **Do not allow bypassing the above settings,** so the rules apply to admins too.
- **Block force pushes and deletion.**

`licenses (all extras)` only runs when dependencies change, so it can't be a required
check: a required check that never runs blocks the PR forever.

Apply the settings with the GitHub CLI:

```bash
gh api -X PUT repos/andrefongkc-cyber/caliper/branches/main/protection --input - <<'EOF'
{
  "required_status_checks": {"strict": true, "contexts": ["core (Linux)", "app (macOS)", "boundaries"]},
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```
