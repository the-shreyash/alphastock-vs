# Security Policy

We take the security of StockAssist AI seriously. This document outlines supported versions, vulnerability reporting procedures, and our security response policies.

---

## Supported Versions

We actively monitor and patch the following versions of StockAssist AI:

| Version | Supported | Notes |
|---------|-----------|-------|
| 1.2.x   | Yes       | Active hardening and patch releases. |
| 1.1.x   | Yes       | Critical security patches only. |
| < 1.1.0 | No        | Unsupported. Please upgrade to a supported release. |

---

## Reporting a Vulnerability

If you discover a security vulnerability in StockAssist AI, please report it privately. Do **not** open a public GitHub issue or discuss it publicly until we have had an opportunity to address the issue.

### Submission Channel
Please email your report to: **security@stockassist.ai**

To help us investigate and patch the issue as quickly as possible, please include the following details in your report:
- **Description:** A detailed explanation of the vulnerability and its potential impact.
- **Environment:** Software versions, operating systems, and configurations affected.
- **Proof of Concept (PoC):** Step-by-step instructions or sample code to reproduce the issue.
- **Logs / Screenshots:** Any relevant stack traces, HTTP requests/responses, or console logs.

---

## Security Response Policy

Upon receiving a private vulnerability report, we commit to the following response timelines:

1. **Acknowledgment:** We will acknowledge receipt of your report within **24 hours** of submission.
2. **Triage:** We will investigate and classify the severity of the vulnerability (Low, Medium, High, Critical) within **48 hours**.
3. **Patch SLA:** We aim to release a resolved patch according to the following guidelines:
   - **Critical / High:** Resolved within **7 days**.
   - **Medium:** Resolved within **14 days**.
   - **Low:** Resolved within **30 days**.
4. **Disclosure:** Once the patch is deployed and verified, we will publish a security advisory detailing the vulnerability and credit the researcher.

---

## Responsible Disclosure Guidelines

To protect our users and infrastructure, we request that researchers follow these responsible disclosure rules:
- Provide us with a reasonable amount of time to address the vulnerability before making it public.
- Do not perform destructive actions, such as degrading server performance (DoS/DDoS) or accessing another user's private financial data.
- Avoid violating any privacy laws or disrupting user sessions.
