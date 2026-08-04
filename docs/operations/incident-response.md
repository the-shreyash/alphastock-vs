# Incident Response

How an incident is declared, escalated and closed. The recovery *procedures*
themselves live in [`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md); this page is
the wrapper around them.

## The loop

```
Detection → Declaration → Diagnosis → Recovery → Verification → Postmortem
```

1. **Detect.** Alert, uptime check, failed cron mail, or a user. Until PH2.10's
   alerting lands, detection is manual and is the largest single component of
   every recovery time — see DISASTER_RECOVERY.md §4.2.
2. **Declare.** Assign a severity (§5.1) and **write down the start time and
   the symptom** before touching anything. Ten seconds now is the difference
   between a postmortem and an anecdote.
3. **Diagnose.** One command, four layers:
   ```bash
   ./scripts/dr/dr_verify.sh --level full
   ```
   The failing layer names the runbook. Resist the theory you formed in the
   first sixty seconds.
4. **Recover.** Follow the runbook (DISASTER_RECOVERY.md §7). Do not improvise a
   destructive step that is not in one; escalate instead.
5. **Verify.** The post-recovery checklist (§9.2). An incident is not closed
   because the system went quiet.
6. **Postmortem.** SEV-1 and SEV-2, within five working days, using
   [`../runbooks/POSTMORTEM_TEMPLATE.md`](../runbooks/POSTMORTEM_TEMPLATE.md).

## Severity and escalation

Defined once, in DISASTER_RECOVERY.md §5. Summary:

| Sev | Meaning | Response |
|---|---|---|
| SEV-1 | Total outage, or **any** data loss or exposure | Immediate; wake people |
| SEV-2 | Degraded but serving | Within the hour |
| SEV-3 | Contained, no user impact | Next business day — still gets a ticket |

Two rules worth repeating here because they are the ones that get broken under
pressure:

* **Any suspected data loss is SEV-1**, however few users it appears to touch.
  "Only one account" is an assessment made before anyone knew.
* **A restore is a decision to accept data loss**, and it is not the on-call
  engineer's decision to make alone at 03:00. Escalate to the engineering lead
  first (§5.2).

## What not to do first

* `docker compose restart` — destroys the evidence and fixes a class of problem
  `restart: unless-stopped` has already tried.
* `docker compose down -v` — the one command in this repository that destroys
  data. It is never the response to an outage.
* Cleaning up a possibly-compromised host. Isolate and escalate (§7 R9);
  evidence has a shorter half-life than the outage.

## Security incidents

A suspected compromise changes the recovery rules completely — recover onto
clean infrastructure from the off-host copy, rotate everything, preserve
evidence. See DISASTER_RECOVERY.md §7 R9 and `.claude/SECRETS.md` for the
leaked-credential procedure.
