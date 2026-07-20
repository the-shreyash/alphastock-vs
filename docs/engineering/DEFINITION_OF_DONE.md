# Definition of Done (DoD)

## Purpose
To ensure that a feature or fix is fully complete, tested, and ready for production deployment before being considered "Done".

## Rules
- A task cannot be moved to "Done" unless all items on the DoD checklist are complete.
- Bypassing the DoD is only permitted in severe hotfix scenarios, and must be backfilled immediately.

## Responsibilities
- **Engineer:** Ensure the code meets all DoD criteria.
- **Code Reviewer:** Verify the DoD is met during code review.
- **QA/Product:** Final verification in a staging environment (if applicable).

## Checklist
- [ ] Code is merged into the `main` branch.
- [ ] Unit and integration tests are written and passing.
- [ ] Code has been reviewed and approved.
- [ ] Documentation (README, API docs, Architecture) is updated.
- [ ] Feature is deployed to a staging or production environment.
- [ ] Acceptance criteria from the ticket have been verified by a second person.

## Approval Workflow
1. Engineer marks PR as ready for review.
2. Reviewer confirms DoD during review.
3. Code is merged.
4. QA/Product validates the acceptance criteria.
5. Ticket is closed and marked "Done".
