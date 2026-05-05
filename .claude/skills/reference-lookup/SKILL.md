---
name: reference-lookup
description: Search the five vendored read-only submodules under references/ for a symbol, register, file, or topic and return file:line citations. Consults each submodule's CLAUDE.md orientation note first, then falls back to grep. Read-only — never modifies anything (IL-2).
---

# reference-lookup

The parent project at `D:\0-Nouslogic\AmazingHand-ARM101-Follower` vendors five upstream repos under `references/`. They are **read-only** per IL-2. This skill is the fast path to "where is X in upstream?" without burning context on broad searches.

## Invocation

User runs `/reference-lookup <topic>` or asks a question like "where is SOFollower defined?" or "what's the SCS0009 goal_position register address?". The agent dispatches per the routing table below.

## 1. Read the orientation notes first

Every submodule has a per-agent orientation note (untracked, gitignored). Read the relevant one before grepping:

| Submodule | Orientation note |
| --- | --- |
| lerobot | `references/lerobot/CLAUDE.local.md` (upstream tracks its own `CLAUDE.md`) |
| AmazingHand | `references/AmazingHand/CLAUDE.md` |
| rustypot | `references/rustypot/CLAUDE.md` |
| FeetechServo | `references/FeetechServo/CLAUDE.md` |
| FTServo_Python | `references/FTServo_Python/CLAUDE.md` |

Each note already indexes top-level layout, key file paths, API surface, and gotchas. The answer to most lookups is already there.

## 2. Topic-to-submodule routing table

| Topic / keyword in query | Primary submodule | Secondary |
| --- | --- | --- |
| `SOFollower`, `Robot` ABC, `RobotConfig`, `register_subclass`, `make_device_from_device_class` | lerobot | — |
| `FeetechMotorsBus`, `Motor`, `MotorNormMode`, `OperatingMode` | lerobot | FTServo_Python (low-level) |
| `lerobot_calibrate`, `lerobot_find_port`, `calibrate()`, calibration JSON path, `HF_LEROBOT_HOME` | lerobot | — |
| Plugin discovery, `lerobot_robot_*` prefix, `register_third_party_plugins` | lerobot | — |
| `Scs0009PyController`, `Sts3215PyController`, rustypot Python API | rustypot | — |
| `sync_read`, `sync_write` from Python | rustypot | FTServo_Python |
| AmazingHand finger gestures, `MiddlePos`, ID-pair-per-finger layout | AmazingHand | — |
| AmazingHand assembly, CAD, wiring | AmazingHand | — |
| SCS0009 register addresses, register map | rustypot (`src/servo/feetech/scs0009.rs`) | FeetechServo (`SCSCL.h`), FTServo_Python (`scservo_sdk/scscl.py`) |
| STS3215 register addresses | FeetechServo (`SMS_STS.h`) | FTServo_Python (`scservo_sdk/sms_sts.py`) |
| Feetech instruction bytes (PING, READ, WRITE, SYNC_READ, SYNC_WRITE) | FeetechServo (`INST.h`) | FTServo_Python (`scservo_def.py`) |
| Packet structure / checksum / on-wire bytes | FeetechServo (`SCS.h` / `SCS.cpp`) | — |
| `PortHandler`, `protocol_packet_handler`, `GroupSyncRead`, `GroupSyncWrite` | FTServo_Python | — |

When the topic could be answered from multiple submodules, prefer the one the parent project actually uses (rustypot for SCS0009, lerobot's `feetech-servo-sdk` shim for STS3215). Use the others as cross-references.

## 3. Search procedure

1. **Parse the topic.** Extract the symbol(s), file extension hint, or domain keywords.
2. **Route.** Pick the primary submodule per §2.
3. **Read the orientation note.** Often the file:line is already listed there.
4. **Grep if needed.** Example commands the agent might run via the `Grep` tool:
   - `pattern: "class SOFollower\\b"`, `path: "references/lerobot"`, `output_mode: "content"`, `-n: true`, `-C: 2`
   - `pattern: "Scs0009PyController"`, `path: "references/rustypot"`, `output_mode: "files_with_matches"`
   - `pattern: "SMS_STS_GOAL_POSITION_L"`, `path: "references/FeetechServo"`, `output_mode: "content"`, `-n: true`
5. **Verify with `Read`.** Open the matched file at the hit's line, read a 5–10 line window for context.
6. **Cite.** Every claim gets a `path:line` citation.

## 4. Output format

```
Topic: <user's query>
Submodule(s) searched: <list>

Answer:
<2–4 sentence summary>

Citations:
- references/lerobot/src/lerobot/robots/so_follower/so_follower.py:37   SOFollower class definition
- references/lerobot/src/lerobot/robots/so_follower/config_so_follower.py:45   SOFollowerRobotConfig dataclass

Next pointers (optional):
- For the gripper (motor 6) handling the parent overrides, see src/arm101_hand/robots/so101_follower_no_gripper.py
```

Keep total output ≤ 30 lines. If more detail is needed, return file:line — do not embed long quotes.

## 5. Refusal / escalation cases

- **User asks to modify a file under `references/`** → Refuse. Cite IL-2 (`docs/conventions/00-iron-laws.md`). Suggest the adapt-by-transferring pattern from `01-module-layering.md`: copy the relevant logic into `src/arm101_hand/` or `scripts/` and own it there.
- **Symbol genuinely doesn't exist** → Say so plainly. Do not invent. Suggest the user check whether they meant a different submodule (route via §2) or a different upstream version.
- **Topic spans multiple submodules and the user wants synthesis** → Return per-submodule sections; do not blur the boundary. Each submodule has a different licence and a different upstream maintainer — keep their context separate.
- **Question is about the parent project, not upstream** → Redirect: this skill is for `references/`. The parent's own code lives under `src/arm101_hand/` and `scripts/` and is searchable with the regular Grep tool.

## 6. Performance notes

- The orientation notes (`CLAUDE.md` / `CLAUDE.local.md` per submodule) are typically 80–150 lines each. Reading one is cheap and almost always the right first step.
- Use `output_mode: "files_with_matches"` for broad-scope queries first; only escalate to `output_mode: "content"` once you know which file(s) are interesting.
- For C++ headers in FeetechServo, prefer `.h` files over `.cpp` for register address lookups — the addresses are constants in headers.

## 7. What this skill does NOT do

- Does **not** modify anything anywhere (IL-2 for `references/`; out of scope for parent code).
- Does **not** install or build any submodule — those are pre-built (lerobot via `uv sync`, rustypot via PyPI wheel, AmazingHand/FeetechServo/FTServo_Python are reference-only).
- Does **not** synthesize cross-submodule patches — that's the parent project's job per `01-module-layering.md`.
