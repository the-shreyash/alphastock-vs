# Testing

How the test suites are built, classified and run.

| Document | Contents |
|---|---|
| [TEST_ARCHITECTURE.md](TEST_ARCHITECTURE.md) | The developer reference: suite layout, hermeticity mechanisms, markers, fixtures, commands, coverage. Start here. |
| [PH3.1_TEST_CERTIFICATION.md](PH3.1_TEST_CERTIFICATION.md) | The PH3.1 certification record (backend): inventory, classification, defects found, coverage baseline, handoff. |
| [PH3.2_FRONTEND_TEST_CERTIFICATION.md](PH3.2_FRONTEND_TEST_CERTIFICATION.md) | The frontend certification record: framework choice and the alternatives rejected, test architecture, per-area coverage, mocking strategy, defects found, handoff. |

**Policy** — what must be tested and to what standard — lives in
`.claude/TESTING.md`. This folder covers *how the suite works*.

**Scope note:** both suites are covered now. The backend suite (`pytest`, 1,035
hermetic tests) is documented in TEST_ARCHITECTURE.md; the frontend suite (Jest +
React Testing Library, 313 tests) in the PH3.2 certification above. They run
independently and share no fixtures.

**Numbering note:** the frontend sprint was briefed as "PH3.2" but corresponds to
`PRODUCTION_ROADMAP.md`'s **PH3.3**; that roadmap's PH3.2 is *Mock Data
Eradication*, which is unrelated and still open.
