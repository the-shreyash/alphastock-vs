# Repository Standards

## Purpose
To define the baseline expectations for the structure, cleanliness, and maintenance of the repository.

## Rules
- All new directories must contain a `README.md` explaining their purpose.
- Temporary files, build artifacts, and secrets must never be committed. Ensure `.gitignore` is up to date.
- The `main` branch must always remain deployable and stable.
- Large binary files must use Git LFS.

## Examples
**Valid Repository Structure:**
```
/docs
/src
/tests
/.github
README.md
```

## Best Practices
- Keep the repository root clean. Place configuration files in appropriate subdirectories where possible.
- Run linters and tests before committing.
- Delete stale or merged branches regularly.

## Common Mistakes
- Committing `.env` files or API keys.
- Leaving "WIP" (Work In Progress) branches unmerged for weeks.
- Adding large dataset files without Git LFS.
