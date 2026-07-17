# Merge Policy

## Purpose
To define when and how code should be merged into the `main` branch to maintain stability.

## Rules
- Direct pushes to `main` are strictly prohibited.
- All code must enter `main` via a Pull Request (PR).
- PRs must have at least one approved review.
- All CI status checks (tests, linters, security scans) must pass.
- Branch must be up to date with `main` before merging (no conflicting changes).

## Responsibilities
- **Engineer merging:** Responsible for ensuring the merge strategy is correct and monitoring the deployment immediately after the merge.

## Checklist
- [ ] PR is approved.
- [ ] CI pipeline is green.
- [ ] Code has been tested in a staging/dev environment if applicable.
- [ ] No unresolved comments exist.

## Approval Workflow
- Use **Squash and Merge** for feature branches (keeps `main` history clean).
- Use **Rebase and Merge** if the branch contains multiple atomic commits that must be preserved individually.
- Do NOT use a standard merge commit unless explicitly required for release coordination.
