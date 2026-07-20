# Code Review Guide

## Purpose
To ensure code quality, share knowledge across the team, and prevent bugs from reaching production.

## Rules
- All pull requests (PRs) must be reviewed by at least one engineer before merging.
- PRs touching sensitive areas (Auth, Payments, Architecture) require two approvals.
- Code authors must review their own code before requesting a review.
- Reviewers must verify that the PR meets the Definition of Done.

## Responsibilities
**Author:**
- Provide a clear PR description explaining the "why".
- Ensure CI passes.
- Respond to comments constructively and promptly.

**Reviewer:**
- Check for logic errors, performance issues, and readability.
- Verify tests cover the new changes.
- Ensure coding standards are followed.
- Approve promptly or request changes with clear, actionable feedback.

## Checklist
- [ ] Does the code solve the problem outlined in the issue?
- [ ] Is the code readable and maintainable?
- [ ] Are there adequate unit/integration tests?
- [ ] Have edge cases been handled?
- [ ] Is the documentation updated?

## Approval Workflow
1. Author opens PR.
2. Automated checks (CI/CD) run.
3. Reviewer reviews code.
4. Changes requested -> Author fixes -> Reviewer re-reviews.
5. Reviewer Approves.
6. PR is merged.
