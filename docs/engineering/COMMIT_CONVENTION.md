# Commit Convention

## Purpose
To maintain a readable, searchable, and automated Git history using Conventional Commits.

## Rules
- All commit messages must follow the [Conventional Commits](https://www.conventionalcommits.org/) format.
- Format: `<type>(<optional scope>): <description>`
- Allowed types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`.
- The description must use the imperative mood (e.g., "add", not "added").
- Include breaking changes in the footer with `BREAKING CHANGE:`.

## Examples
- `feat(auth): add google oauth provider`
- `fix(ui): resolve button alignment issue on mobile`
- `docs(readme): update setup instructions`

## Best Practices
- Keep commits atomic and focused on a single logical change.
- Use the body of the commit message to explain *why* a change was made, not *what* was changed (the diff shows the *what*).
- Reference issue trackers in the footer (e.g., `Closes #123`).

## Common Mistakes
- Using past tense: `fixed the bug`.
- Combining unrelated changes into one huge commit: `feat: add login and fix database timeout`.
- Missing scopes when the change affects a specific module.
