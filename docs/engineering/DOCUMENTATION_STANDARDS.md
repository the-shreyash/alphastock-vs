# Documentation Standards

## Purpose
To ensure documentation remains accurate, highly discoverable, and acts as a single source of truth across the organization.

## Rules
- **Documentation Synchronization Rule:** Before creating any new document, check whether equivalent information already exists. Reuse, reference, or relocate existing information instead of duplicating it.
- All folders must have a `README.md` defining their purpose and contents.
- Markdown is the standard format for all technical documentation.
- Architecture changes must be documented before merging code.

## Examples
- When adding a new architecture diagram, update `docs/architecture/README.md` and remove any outdated equivalent diagrams elsewhere.
- When creating a new module, ensure its API is documented in `docs/architecture/API_REFERENCE.md`.

## Best Practices
- Keep documents concise and easily skimmable.
- Use Mermaid.js for diagrams inside Markdown.
- Update internal links whenever a document is moved or renamed.

## Common Mistakes
- Creating `ARCHITECTURE_v2.md` instead of updating the existing document.
- Writing code first and documenting "later" (which often means never).
- Duplicating onboarding steps in both the repository root and a wiki.
