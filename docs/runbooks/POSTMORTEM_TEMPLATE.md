# Postmortem — <short incident title>

> Copy this file to `docs/runbooks/postmortems/YYYY-MM-DD-<slug>.md` and fill it
> in. Required for every SEV-1 and SEV-2 within **five working days**
> (`docs/operations/DISASTER_RECOVERY.md` §10).
>
> **This document is blameless by construction.** Its output is a list of
> changes to the *system* — a runbook edit, a new check, an alert, a guard rail.
> "Be more careful" is never an action item; if it appears here, the analysis
> stopped one question early. Every operator in this document acted reasonably
> given the information they had at the time. The interesting question is always
> why the information was what it was.
>
> Delete this block before publishing.

| | |
|---|---|
| **Status** | draft / in review / final |
| **Severity** | SEV-1 / SEV-2 / SEV-3 |
| **Incident date** | YYYY-MM-DD |
| **Duration** | Xh Ym (detection → recovery verified) |
| **Author** | |
| **Reviewers** | |
| **Runbook(s) used** | R1 / R4 / … (or "none — no runbook covered this") |

---

## 1. Summary

Three or four sentences, readable by someone who was not involved and does not
work on this system. What broke, who it affected, for how long, and how it was
resolved. No jargon, no root cause yet.

---

## 2. User impact

Be specific. "Some users may have been affected" is not an impact statement.

| | |
|---|---|
| **Users affected** | how many, which segment |
| **Functionality affected** | what did not work |
| **Market hours?** | yes/no — a trading platform's outage cost is not uniform across the day |
| **Data loss** | **none** / RPO window of X hours: what exactly was lost |
| **Data exposure** | none / describe, and note whether the security lead was engaged |
| **Communicated?** | when, where, by whom — or why not |

**If any data was lost, state the window in wall-clock terms** ("writes between
03:15 and 09:40 UTC on 2026-08-04") rather than "up to 24 hours". Users can act
on the first and cannot act on the second.

---

## 3. Timeline

All times UTC. Include the moments where someone was *wrong* — a discarded
theory is usually the most informative line in a postmortem, and omitting it is
how the same wrong turn gets taken again.

| Time | Event |
|---|---|
| 03:15 | *(what actually started it — often earlier than detection)* |
| 09:40 | Detected: how? alert / user report / someone noticed |
| 09:44 | Declared SEV-N, incident log opened |
| 09:47 | `dr_verify.sh --level full` → layer N failing |
| 09:52 | Hypothesis: … (later found incorrect) |
| 10:05 | Recovery action: … |
| 10:12 | Verified: `dr_verify.sh --level full` exits 0 |
| 10:20 | Users notified / incident closed |

Two derived numbers, because they are what improves:

* **Time to detect (TTD):** ____
* **Time to recover (TTR), from detection:** ____

---

## 4. Root cause

Ask "why" until the answer is a property of the system rather than a property of
a person. Contributing factors are not the same as the root cause — list both,
and be honest about which is which.

**Trigger:** what set it off.

**Root cause:** the condition that made the trigger capable of causing this.

**Contributing factors:** what made it worse, slower to detect, or harder to
diagnose than it needed to be.

---

## 5. What worked

Do not skip this section. Controls that worked are the ones most likely to be
removed later by someone who never saw them save an incident, and this is the
only record that they did.

* e.g. verify-before-write meant the corrupt artifact was caught before anything
  was overwritten
* e.g. the `--expect-manifest` comparison caught a restore that had moved nothing
* e.g. `restart: unless-stopped` absorbed the first two failures without a human

---

## 6. What did not work

The system, not the people.

* e.g. detection was a user report, 6 h after the fact — no alert exists for this
* e.g. the runbook's step 4 assumed the stack was running; it was not
* e.g. `dr_verify.sh` passed while the system was broken → the missing check is
  an action item below

---

## 7. Where luck was involved

The most valuable section and the one most often left out. Name every place the
outcome would have been materially worse under a small, plausible change:
someone happened to be awake; the backup happened to be four hours old rather
than twenty-three; the previous image happened not to have been pruned.

**Every line here is an unowned risk with a good outcome so far.** Each one
should produce an action item or an explicit, written decision to accept it.

---

## 8. Action items

Owner and date, or it is a wish. Prefer changes that make the failure
*impossible* or *loud* over changes that make it *less likely*.

| # | Action | Type | Owner | Due | Status |
|---|---|---|---|---|---|
| 1 | | prevent / detect / mitigate / document | | | |
| 2 | | | | | |

**The postmortem is not finished until at least one action item is a change to
a runbook, a new check in `dr_verify.sh`, a new alert, or a new drill.** An
incident that produces only prose has taught the system nothing — only the
people who were there, and only until they forget or leave.

---

## 9. Documentation updated

- [ ] `docs/operations/DISASTER_RECOVERY.md` — runbook corrected / added
- [ ] `docs/operations/BACKUP_AND_RESTORE.md`
- [ ] `docs/operations/MONITORING.md` — new alert or probe
- [ ] `scripts/dr/dr_verify.sh` — new check
- [ ] `.claude/TASK.md` / `.claude/CHANGELOG.md`
- [ ] N/A — with the reason: ____
