---
name: doc_update
description: Refresh CLAUDE.md (agent self-reference) and README.md (human-facing) at the repo root after architectural or workflow changes. Use when subsystems are added/removed, hardware setup changes, pyproject.toml dependencies change materially, an Iron Law is reworded, or before tagging a release. Does NOT update files inside docs/conventions/ — those go through normal PRs.
---

# doc_update

Keep `CLAUDE.md` and `README.md` at the repo root synchronized with the current state of the codebase. This skill is the invokable form of the **Documentation Protocol** in `docs/conventions/06-documentation-protocol.md` — that file holds the normative content. This skill is the thin hand-off.

## Invocation

User runs `/doc_update`. The agent follows the steps below.

## Procedure

### 1. Read the protocol

Before doing anything, read the normative protocol once so the behavior matches the written rules:

```
docs/conventions/06-documentation-protocol.md
```

If the procedure below diverges from that file, the file wins. Raise the inconsistency instead of proceeding.

### 2. Pre-flight verification (local; run it yourself)

This is a single-PC Windows project — there is no remote board, so all pre-flight checks run on the dev host. Run these directly:

```powershell
git log --oneline -20
git ls-files docs/conventions/
git ls-files src/arm101_hand/ scripts/
uv pip list
```

Cross-check what these report against what `CLAUDE.md` and `README.md` currently claim. Note every discrepancy.

If the trigger for this run is a **hardware change** (IL-1 voltage, IL-3 motor IDs, IL-4 COM ports, BOM line item), present this command and ask the user to paste output back — you cannot assert physical state:

```powershell
Get-PnpDevice -Class Ports -Status OK | Select-Object Name, DeviceID | Format-Table -AutoSize
```

### 3. Context analysis

Per §3 of the protocol:

1. Project root: `D:\0-Nouslogic\AmazingHand-ARM101-Follower`.
2. Read these files in parallel (use batched `Read` calls):
   - `CLAUDE.md` (current content)
   - `README.md` (current content)
   - `docs/conventions/00-iron-laws.md` (so summaries you touch stay accurate)
   - `docs/conventions/01-module-layering.md` (canonical source layering)
   - `docs/BOM.md` (canonical hardware spec)
3. Scan the codebase tree:
   - `git ls-files src/arm101_hand/` — which device/config/script modules exist? Empty subfolders mean "placeholder, not yet implemented".
   - `git ls-files scripts/calibration/` — which calibration runners are real?
   - `git status` — in-flight work that should be reflected.
4. Identify the gap between current docs and current state. List each discrepancy before drafting.

### 4. Draft CLAUDE.md updates

Per §4 of the protocol:

- Target length: **≤ 250 lines** (protocol §4 / §12.1).
- Required sections in this order: project TL;DR, Iron Laws TL;DR, directory tree (depth ≤ 3), common workflows, convention file pointers, don'ts.
- **Summarize** Iron Laws in one line each; do not duplicate normative text. Each line implies/ends with a pointer to `docs/conventions/00-iron-laws.md`.
- Distinguish **current** state from **planned** state explicitly with `(planned)` or `(not yet implemented)` markers (protocol §3.1).
- **Format for cross-renderer compatibility** per §6 below — apply before presenting.

### 5. Draft README.md updates

Per §5 of the protocol:

- Target length: **≤ 400 lines** (protocol §12.1).
- Required sections: project title + tagline, what-it-is bullets, hardware requirements (point to `docs/BOM.md`), host setup (`uv sync`), first commands (port discovery + AmazingHand calibration + SO-ARM101 calibration), repo layout, conventions pointer, license.
- Audience: senior engineer new to this repo. Friendly, second-person voice.
- **No Iron Laws** in README.md — those belong in `CLAUDE.md` and `00-iron-laws.md` (protocol §5.2).
- **Format for cross-renderer compatibility** per §6 below — apply before presenting.

### 6. Markdown lint (apply silently before diff)

Every `.md` file this skill produces should render cleanly in GitHub-flavored Markdown and survive Notion's `File → Import → Markdown` importer.

Required formatting:

- **Headings**: H1–H3 for primary structure; H4 sparingly. One blank line before/after every heading.
- **Lists**: `-` for bullets (not `*` or `+`). 2-space nesting. Task lists `- [ ]` import as Notion to-dos.
- **Tables**: GitHub pipe syntax. Header row + `---` separator. **Cells single-line** — Notion does not render `<br/>` or embedded bullets in cells. Rewrite multi-line cells as comma-separated clauses or split into rows. Escape literal pipes as `\|`. Skip alignment markers (`:---:`).
- **Code fences**: triple-backtick with a language tag (`powershell`, `python`, `bash`, `text`, etc.).
- **No inline HTML** outside code fences (`<br/>`, `<sub>`, `<sup>`, `<u>`, `<div>`, `<span>`, `<b>`, `<i>`). Use native Markdown.
- **Block quotes** (`>`) import as Notion callouts. Keep short; `>` on every continuation line.
- **Links**: inline `[text](url)`. Reference-style `[text][1]` is unreliable through Notion import.
- **No emojis** anywhere — project-wide. Use plain-text labels (`WARNING:`, `OK`, `FAIL`, `NOTE:`). GitHub shortcodes (`:warning:`) are emojis by another name and forbidden too. Box-drawing characters inside code fences (`├`, `│`, `└`, `─`, `→`) are NOT emojis and may stay.

Lint-before-diff checklist — grep each draft and fix before presenting:

- `<br/?>` outside fences → rewrite in Markdown.
- `<sub|<sup|<u>|<div|<span|<b>|<i>` → rewrite.
- Lines starting with `*` followed by whitespace (outside code fences) → change to `- `.
- Any table row with mismatched pipe count → fix.
- Any emoji character (Unicode pictographs, dingbats, ✓, warning signs, colored symbols) → delete or replace with a plain-text label.

Fix silently; do not surface formatting churn in the user-facing diff summary.

### 7. Present diffs

Show the user:

1. A concise summary of what changed and why (1–3 bullets per file).
2. The full proposed content for each file if the change is material, or a focused diff if it's a minor edit.
3. Flag any uncertainty — places where the current repo state was ambiguous.

**Wait for explicit approval before writing.**

### 8. Apply

On approval:

1. Use `Edit` (preferred) or `Write` (only on full rewrites) to update `CLAUDE.md` / `README.md` at the repo root.
2. Verify caps: `CLAUDE.md` ≤ 250 lines, `README.md` ≤ 400 lines.
3. Do **not** commit automatically. Suggest a commit message per `docs/conventions/05-git-workflow.md` (scope `docs`):
   ```
   docs: refresh CLAUDE.md and README.md for <change summary>
   ```
4. Wait for the user's go before running `git add` / `git commit`.

## What this skill does NOT do

- Does **not** update files inside `docs/conventions/`. Those go through normal PRs with review (protocol §1 exclusion).
- Does **not** invent content not present in the codebase. If a subsystem isn't on disk (e.g., the `src/arm101_hand/hand/` rustypot wrapper is currently a placeholder), don't claim it exists — mark it `(planned)`.
- Does **not** modify files under `references/` — IL-2 prohibits this.
- Does **not** commit automatically without the user's explicit go.
- Does **not** assert hardware state. For IL-1/IL-3/IL-4 changes, ask the user to run the `Get-PnpDevice` command and paste output.

## Consistency

If the normative protocol in `docs/conventions/06-documentation-protocol.md` is updated, this `SKILL.md`'s hand-off instructions are reviewed in the same PR for consistency.
