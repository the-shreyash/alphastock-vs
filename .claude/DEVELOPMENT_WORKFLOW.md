GLOBAL ENGINEERING RULES

✓ Read documentation before coding.

✓ Never implement more than one sprint at a time.

✓ Never skip verification.

✓ Never leave documentation outdated.

✓ Never introduce unrelated refactors.

✓ Never change architecture without updating documentation.

✓ Preserve backward compatibility unless the sprint explicitly requires breaking changes.

✓ Prefer maintainability over shortcuts.

✓ Generate a sprint completion report after every implementation.

✓ Stop after the assigned sprint.


# ==========================================
# STOCKASSIST AI DEVELOPMENT WORKFLOW
# ==========================================

Every implementation sprint MUST follow this workflow.

==========================================
STEP 1 — Read Documentation
==========================================

Read all required project documentation before writing code.

Minimum:
- INDEX.md
- PROJECT.md
- Current Sprint Document
- SYSTEM_ARCHITECTURE.md
- Relevant Feature Documents

Understand:
- Business requirements
- Architecture
- Constraints
- Existing implementation
- Acceptance criteria

❌ Never start coding without reading documentation.

↓

==========================================
STEP 2 — Analyze
==========================================

Analyze the existing implementation.

Identify:
- Affected modules
- Dependencies
- Risks
- Existing patterns
- Technical debt
- Backward compatibility

↓

==========================================
STEP 3 — Plan
==========================================

Create an implementation plan.

Define:
- Files to modify
- Files to create
- Migration strategy
- Testing strategy
- Rollback strategy

Confirm the work stays within the current sprint.

❌ Never implement future sprints.

↓

==========================================
STEP 4 — Implement
==========================================

Implement ONLY the approved sprint scope.

Rules:
- Follow project architecture
- Keep code production-ready
- Maintain consistency
- Do not introduce unrelated changes
- Do not break existing functionality

↓

==========================================
STEP 5 — Verify
==========================================

Verify implementation.

Run:
- Unit tests
- Integration tests
- Build checks
- Linting
- Type checking
- Manual verification

Fix issues before continuing.

↓

==========================================
STEP 6 — Update Documentation
==========================================

Update only affected documentation.

Examples:
- CHANGELOG.md
- TASKS.md
- ROADMAP.md
- Relevant architecture documents

Do not leave documentation outdated.

↓

==========================================
STEP 7 — Generate Sprint Report
==========================================

Provide:

- Sprint Summary
- Files Modified
- Tests Executed
- Documentation Updated
- Risks
- Known Limitations
- Verification Completed
- Remaining Work

↓

==========================================
STEP 8 — STOP
==========================================

Do NOT continue to the next sprint.

Wait for review and approval before beginning the next implementation.