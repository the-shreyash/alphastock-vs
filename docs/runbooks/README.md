# Runbooks

Incident artifacts — the templates an operator fills in, and the postmortems
they become.

| File | Purpose |
|---|---|
| [`POSTMORTEM_TEMPLATE.md`](POSTMORTEM_TEMPLATE.md) | Blameless postmortem. Required for every SEV-1 and SEV-2 within five working days. |
| `postmortems/` | Completed postmortems, `YYYY-MM-DD-<slug>.md`. Created on first use. |

## Why this is separate from `docs/operations/`

`docs/operations/` holds the **procedures** — what to do, decided in advance,
when nobody is under pressure. This directory holds the **records** — what
actually happened, written afterwards. Mixing them means a runbook and a
one-off incident writeup look equally authoritative to whoever reads the
directory at 03:00.

The recovery procedures themselves live in
[`../operations/DISASTER_RECOVERY.md`](../operations/DISASTER_RECOVERY.md) §7,
next to the recovery objectives and verification checklist they depend on.

## The rule that connects the two

**A postmortem is not finished until it changes something in
`docs/operations/`** — a corrected runbook, a new check in
`scripts/dr/dr_verify.sh`, a new alert, or a new drill. An incident that
produces only prose has taught the system nothing; it has taught the people who
were there, and only until they forget or leave.
