# 05 — Git Workflow

> Branch strategy, commit message format, PR protocol for this repo. Adds one project rule — **atomic cross-device commits** — because the arm and hand share a runtime and silent drift between them is a debuggability failure (IL-6).

---

## 1. Branch Strategy

```
main          # Stable, runnable from a clean clone. Tagged releases cut from here.
  ├── feature/<short-name>    # New features
  ├── bugfix/<issue-ref>      # Bug fixes
  ├── chore/<name>            # Deps, CI, tooling, submodule bumps
  └── docs/<name>             # Documentation-only changes
```

**`main`** is always runnable: a clone + `uv sync` + the calibration commands in repo-root `README.md` should work cleanly at every commit on `main`.

This repo does not (yet) need a separate `develop` integration branch — the team is small and shipping cadence is low. Re-evaluate when more than two people are merging concurrently.

## 2. Branch Naming

| Type | Pattern | Example |
|---|---|---|
| Feature | `feature/<short-description>` | `feature/teleop-bringup` |
| Bug fix | `bugfix/<issue-key-or-description>` | `bugfix/yaml-load-on-empty-finger` |
| Chore | `chore/<description>` | `chore/bump-rustypot-1.5` |
| Docs | `docs/<description>` | `docs/iron-laws-cleanup` |

Keep names short (< 40 chars) and all-lowercase with kebab separators.

## 3. Commit Messages

Conventional Commits format:

```
type(scope): imperative summary in lower case, no trailing period

Optional body — wrap at 72 cols. Explain **why** and the **context**, not
just **what** (the diff shows what). Reference any Iron Laws touched.

Refs: ISSUE-123
```

### 3.1 Types

| Type | Use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Code restructure, no behavior change |
| `perf` | Performance improvement |
| `test` | Test additions or fixes |
| `docs` | Documentation only |
| `chore` | Tooling, deps, submodule bumps, non-source |
| `style` | Formatting only |

### 3.2 Scopes

Subsystem-based, not file-based:

| Scope | Refers to |
|---|---|
| `arm` | `src/arm101_hand/robots/*`, anything SO-ARM101-specific |
| `hand` | `src/arm101_hand/hand/*`, anything AmazingHand-specific |
| `calibration` | Anything under `scripts/calibration/` |
| `config` | `src/arm101_hand/config/*`, pydantic schemas |
| `scripts` | `scripts/*` outside `calibration/` (demos, future teleop runners) |
| `build` | `pyproject.toml`, `.python-version`, `uv.lock` |
| `deps` | `references/` submodule bumps |
| `conventions` | Anything under `docs/conventions/` |
| `docs` | Repo-root `README.md`, `CLAUDE.md`, `docs/BOM.md` |

### 3.3 Good vs bad

```
# Good
feat(arm): subclass SOFollower to drop the missing gripper motor

The AmazingHand replaces ID 6 on the arm bus, so the upstream SOFollower's
hard-coded 6-motor dict crashes on connect. Adds SO101FollowerNoGripper
that overrides __init__ with a 5-motor dict and registers as a new
RobotConfig type. Wired through the new arm101-calibrate-follower
console script which imports the subclass before delegating to lerobot's
draccus-wrapped calibrate().

Touches IL-3 (motor ID canon) — formalizes the ID-6-absent state.
```

```
# Bad — no scope, tense wrong, no "why"
Update some arm code
Fixed the bug
wip
```

## 4. The Atomic Cross-Device Commit Rule (IL-6)

A change that touches both the arm and the hand — shared YAML schema, a coordination script, an API both consume — must land in **one commit** that updates every affected file together.

Reasons:

- The two devices share a single Python process; a partial update creates an inconsistent runtime.
- Bisecting a regression that crosses both devices is much easier when commits are atomic.
- Reviewers can verify both halves match in a single diff.

If a refactor genuinely needs more than ~8 files, split logically (e.g., "introduce schema in shared config module" → "switch hand to schema" → "switch arm to schema"), but each split must independently leave the repo runnable.

This rule does **not** apply to:

- Refactors confined to one device (just-arm or just-hand).
- Documentation changes that mention both devices for context.
- Submodule bumps under `references/` (those are always solo per `chore(deps)`).

## 5. Pull Request Protocol

### 5.1 Opening a PR

- Base branch: `main`.
- Title format matches commit format: `type(scope): summary`.
- Body uses the template below.

### 5.2 PR template

```markdown
## Summary
<1–3 bullets of what this does and why>

## Iron Laws touched
List every Iron Law from `docs/conventions/00-iron-laws.md` this PR affects, and why it does not violate them:
- [ ] IL-1 voltage isolation
- [ ] IL-2 references read-only
- [ ] IL-3 motor ID canon
- [ ] IL-4 COM-port discipline
- [ ] IL-5 calibration in version control
- [ ] IL-6 atomic cross-device commits
- [ ] IL-7 single-source-of-truth docs

## Testing
- [ ] `uv run ruff check` clean
- [ ] `uv run pytest -m 'not hardware'` passes (when tests exist)
- [ ] On-bench check (if hardware behavior changed): describe what you ran + result

## Screenshots / logs
<output of calibration runs, photos of the bench setup if relevant>

Refs: <issue link>
```

### 5.3 Merge strategy

- **Squash merge** from feature branches. Keeps `main` history readable; the detailed commits live on the feature branch if you need to `git blame` deeper.
- **Rebase merge** for trivial chores (typos, dep bumps with one commit).
- **Never rebase merged branches** after they've been merged to `main`.

## 6. Code Review

### 6.1 Required reviewers

- **Any PR**: at least one reviewer who is not the author. (Self-merge OK for solo developer; flag in commit body.)
- **PRs touching both arm and hand**: reviewer should explicitly confirm IL-6 — the atomic commit covers all related changes.
- **PRs touching `references/`**: reviewer confirms the bump references an upstream tag or SHA, not a branch head.

### 6.2 What reviewers check

1. **Iron Laws** — cross-reference the PR's claimed IL list against the actual diff.
2. **Style** — `ruff format` idempotent; `ruff check` clean.
3. **Tests** — new logic has unit tests; on-bench verification described if hardware behavior changed.
4. **Cross-device** — if both arm and hand are touched, is the change atomic?

## 7. Release Process

### 7.1 Version scheme

Semantic versioning: `v<major>.<minor>.<patch>`.

- **Major**: breaking schema or API change.
- **Minor**: new feature, backwards-compatible.
- **Patch**: bug fix only.

### 7.2 Release procedure

1. Tag a release branch `release/v0.X.Y` from `main` (only needed if release prep takes more than one commit; otherwise tag on `main` directly).
2. Update `CHANGELOG.md` (top section = this release; curated, not auto-generated).
3. Run the on-bench integration checklist from `04-testing-verification.md` §5.
4. Tag `v0.X.Y` on `main`.
5. Push the tag.

## 8. Git Submodules (`references/`)

- **Every submodule bump is a separate PR** with a `chore(deps):` scope.
- **Never combine a submodule bump with a feature commit.** If the feature requires the bump, split: bump lands first, feature lands next (and depends on the bump commit in the PR description).
- **Submodule bumps must reference an upstream tag or SHA in the commit body**, never a branch head.
- **Never modify files under `references/`** (IL-2). If you need to adapt code from a reference, transfer it into `src/` or `scripts/` and own it there.

## 9. Rewriting History

- **Never `git push --force` to `main`**. Hotfixes use revert commits, not force-pushes.
- **Feature branches may `git rebase`** before opening a PR to tidy history — once the PR is open, stop rebasing (it breaks review comment threading).
- **`git commit --amend`** is fine on your local branch before pushing. After pushing, prefer a new commit.

## 10. `.gitignore`

Repo-level `.gitignore` covers Python (`__pycache__/`, `.venv/`, `*.egg-info/`), IDE (`.vscode/`, `.idea/`), OS (`.DS_Store`, `Thumbs.db`), local artifacts (`*.log`, `.tmp/*`), and the Claude Code local settings file.

`uv.lock` **is** committed.

## 11. Secrets

- **No credentials in the repo, ever** — not in code, not in configs, not in test fixtures.
- HuggingFace tokens and similar live in `~/.cache/huggingface/token` outside the repo.
- If a secret is accidentally committed: rotate the secret immediately, then `git filter-repo` to strip history, then force-push (the single documented exception to "never force-push").

---

## 12. Checklist (for PR authors)

- [ ] Branch name follows `type/short-description`
- [ ] Commits follow `type(scope): summary` with body when non-trivial
- [ ] Cross-device changes (arm + hand) land atomically (IL-6)
- [ ] PR body uses the template; Iron Laws section accurate
- [ ] `ruff format` + `ruff check` clean locally
- [ ] Squash merge for feature → main; rebase for trivial chores
- [ ] No submodule bumps mixed with feature commits
- [ ] No credentials, no binaries > 5 MB
