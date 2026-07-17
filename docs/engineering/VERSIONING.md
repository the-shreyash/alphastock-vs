# Versioning

## Purpose
To provide a predictable and standardized way of communicating changes in the software to users and dependent systems.

## Rules
- We follow [Semantic Versioning (SemVer)](https://semver.org/).
- Version format: `MAJOR.MINOR.PATCH`
- **MAJOR** version when you make incompatible API changes.
- **MINOR** version when you add functionality in a backward compatible manner.
- **PATCH** version when you make backward compatible bug fixes.
- The `v` prefix must be used for Git tags (e.g., `v1.2.4`).

## Examples
- `v1.0.0` -> `v1.0.1` (Bugfix in existing feature)
- `v1.0.1` -> `v1.1.0` (Added a new endpoint)
- `v1.1.0` -> `v2.0.0` (Changed the data model in a breaking way)

## Best Practices
- Automate version bumping in CI/CD pipelines based on Conventional Commits.
- Write release notes for every MINOR and MAJOR release.

## Common Mistakes
- Bumping MINOR instead of MAJOR when introducing breaking changes because "it was just a small breaking change."
- Forgetting to tag releases in Git.
