# Release Policy

## Purpose
To define the process for deploying changes to production safely and predictably.

## Rules
- Releases are cut from the `main` branch.
- Every release must be tagged with a Semantic Version (`vX.Y.Z`).
- A changelog must be generated for every release.
- Releases should preferably happen during standard working hours.
- A rollback plan must be available for every major release.

## Responsibilities
- **Release Manager (or designated engineer):** Coordinates the release, writes the release notes, and monitors the deployment.
- **On-call Engineer:** Monitors alerts and metrics post-deployment.

## Checklist
- [ ] All features slated for release meet the Definition of Done.
- [ ] Staging environment matches production and has been verified.
- [ ] Git tag created and pushed.
- [ ] Release notes published (CHANGELOG.md updated).
- [ ] Production deployment triggered and completed successfully.
- [ ] Post-deployment sanity check passed.

## Approval Workflow
1. Release Manager verifies all PRs for the release are merged.
2. Release candidate is tested in staging.
3. Git tag is pushed, triggering production deployment.
4. Release notes are broadcasted to the team/stakeholders.
