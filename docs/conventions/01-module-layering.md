# 01 — Module Layering Discipline

> Every module in `src/arm101_hand/` and `scripts/` belongs to exactly one abstraction layer. Dependencies only flow downward. This is a naming of what the directory layout already does — not a change to it.

---

## 1. The Layers

| Layer | Role | Directories / files |
|---|---|---|
| `primitive` | Stateless helpers, pure logic, schemas. No hardware contact. | `src/arm101_hand/config/` (pydantic schemas, lookup tables) |
| `device` | Owns one external interface; speaks that interface's vocabulary. One hardware abstraction per file. | `src/arm101_hand/robots/so101_follower_no_gripper.py` (Feetech bus → arm), `src/arm101_hand/hand/*.py` (rustypot wrappers → fingers) |
| `application` | Top-level scripts and console-script entry points. Wires devices into a runnable workflow. | `src/arm101_hand/scripts/calibrate_follower.py`, anything in `scripts/` |

A small Python project does not need a 5-layer architecture. Three layers is enough — but the import direction (primitive ← device ← application) is non-negotiable.

---

## 2. The Four Import Invariants

Every PR that touches `src/arm101_hand/` or `scripts/` must satisfy all four:

**1. No upward imports.**
`primitive` does not import from `device` or `application`. `device` does not import from `application`. The dependency graph is a DAG that points downward.

**2. No same-layer peer imports, except orthogonal primitives.**
Two `device` modules do not import each other. If `arm + hand` ever need to coordinate, the orchestration belongs in an `application` script — not in either device's source.
A `primitive` shared by several modules (a config schema, a unit conversion) is the one allowed exception, and it must stay a true primitive (stateless, no side effects).

**3. No skip-layer imports across hardware.**
An `application` script may import a `device` directly (it has no other choice — there's nothing between them in our 3-layer model). But it must not reach into a device's internals: only the device's public API.

**4. Config is data, not behavior.**
A file under `src/arm101_hand/config/` contains only declarations: pydantic models, enums, default tables. Zero hardware contact. Zero side effects. Zero I/O at import time.
If a config file needs a function to compute a derived value, that function belongs in the module it serves, not in the config file.

---

## 3. When a `device` module gets a config sibling

A device gets a sibling config (a pydantic model under `src/arm101_hand/config/`) only if it carries **tunable parameters** — numbers, thresholds, defaults that an integrator might legitimately change without touching logic. Examples that would qualify:

- `so101_follower_no_gripper`: per-motor velocity limits, default port, timeout.
- A future hand-control wrapper: per-finger middle_pos defaults, close/open angle deltas, speed.

Modules with no tunables do **not** get a config sibling. Absence is information — it tells the reader there is nothing to tune here.

---

## 4. Hardware must not be contacted at import time

Any module that owns a hardware resource (serial port, COM handle, rustypot controller) must:

- Not open the resource at module scope, in a class default, or at import time.
- Accept the resource via constructor injection (lerobot's `RobotConfig` pattern is correct) or open it in an explicit `connect()` / `__enter__()` method.

This ensures every module under `src/arm101_hand/` is importable with no hardware attached, which is required for the host unit tests in `04-testing-verification.md` to run anywhere.

The `SO101FollowerNoGripper.__init__` constructs the `FeetechMotorsBus` but does not open the port — that happens in inherited `connect()`. Same pattern applies to any hand wrapper we add later.

---

## 5. Rationale

The discipline keeps changes local: an edit inside one device does not cascade into application scripts, and the mental model for reading any single file is "what does *this layer* know?" rather than "what does the whole system do?"

It also makes host-only testing possible: because hardware contact is forbidden at import time, every module can be imported and its pure-logic methods exercised by `pytest` without a live bus. This is what makes the write→test→fix loop fast enough to catch mistakes before they compound.

---

## 6. Enforcement

- Reviewer-enforced at PR time. The import graph is visible in under a minute via `grep -r "^from arm101_hand" src/`.
- No CI check yet — a future `scripts/lint/check_layers.py` would automate the graph walk and fail on any upward / cross-device edge.
- Violations that are genuinely necessary (e.g., a one-line shortcut that saves a real maintenance burden) must be documented inline with a comment naming the invariant being broken and why.
