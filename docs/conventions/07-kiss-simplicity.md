# 07 — KISS: Keep It Simple

*Systems work best kept simple, not complex. Simplicity is harder to produce than
complexity — it is the work, not the shortcut. Less is more; simplicity wins in the
long run.*

**The rule:** Prefer the simplest design that satisfies the requirement and the Iron
Laws. When two designs both work, ship the one with fewer files, fewer concepts, and
less indirection.

## Why it pays off

- **Maintainable** — fewer moving parts to reason about.
- **Readable** — a new contributor (or future you) follows it without untangling clever patterns.
- **Debuggable** — straightforward control flow makes bugs visible. On hardware that
  moves, a bug you can *see* is a bug that does not damage a servo.
- **Extensible** — a simple foundation extends cleanly; a clever one resists change.

## In practice

1. **One responsibility per unit** — small functions/modules that each do one thing.
2. **Readability over cleverness** — write as if explaining to a less-experienced developer.
3. **No speculative abstraction (YAGNI)** — add structure when a second real case appears, not before.
4. **No premature optimization** — make it correct and clear first.
5. **Refactor toward simpler** — leave code simpler than you found it.
6. **Mirror existing shapes** — when one device already solves a problem (e.g., the
   hand's single `poses` section), the other should match rather than invent a parallel scheme.

## Smells of over-complexity

- The same data in two places with divergent readers.
- A config section nothing writes.
- A "flexible" layer with exactly one caller.
- Needing a paragraph to explain why two files exist.
