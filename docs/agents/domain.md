# Domain Docs

Engineering skills should consume this repository's domain documentation before exploring related code.

## Before exploring, read these

- `CONTEXT.md` at the repository root.
- Relevant ADRs under `docs/adr/`.

If these files do not exist, proceed silently. Domain-modeling skills create them lazily when terminology or decisions are resolved.

## Layout

This is a single-context repository:

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md` in issues, proposals, hypotheses, and test names. Avoid synonyms that the glossary explicitly rejects.

If a needed concept is absent, reconsider whether the term belongs or note the gap for domain modeling.

## Flag ADR conflicts

Explicitly identify output that contradicts an existing ADR rather than silently overriding it.
