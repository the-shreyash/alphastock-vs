# Branching Strategy

## Purpose
To ensure a consistent, conflict-free workflow for developing features, fixing bugs, and cutting releases.

## Rules
- Use `main` as the single source of truth for production code.
- Create feature branches from `main`.
- Branch names must follow the format: `<type>/<issue-number>-<short-description>`.
- Allowed types: `feature`, `bugfix`, `hotfix`, `chore`, `docs`.

## Examples
- `feature/102-add-login-page`
- `bugfix/204-fix-payment-crash`
- `hotfix/production-auth-bypass`
- `docs/update-architecture-diagram`

## Best Practices
- Keep branches short-lived (merge within a few days).
- Rebase frequently against `main` to avoid large merge conflicts.
- Delete branches immediately after merging.

## Common Mistakes
- Naming a branch simply `my-feature` without an issue number or type.
- Developing multiple unrelated features on a single branch.
- Merging `main` into the feature branch instead of rebasing (creating messy history).
