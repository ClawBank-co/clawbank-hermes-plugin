# Safety reference

The confirmation contract, the per-area confirmation matrix, the failure
patterns to avoid, and the known limits all live in
[`../SKILL.md`](../SKILL.md) — the skill is deliberately self-contained so
that every safety-critical rule is available through `skill_view` in a live
session, where supplementary files may not be loadable.

This file exists for human readers navigating the repository. Do not add
safety rules here; add them to `SKILL.md`.
