---
title: Engineering Guide
---

# Engineering Guide

This guide describes how the platform team ships changes. Read it before your first pull request.

## Branches and reviews

Every change lands through a pull request. A reviewer from another team approves it, and the
author merges it after the checks pass.

- Keep pull requests small: under 400 changed lines.
- Write the test first when fixing a bug.
  - The test must fail before the fix.
  - It must pass after.
- Squash on merge.

### Release train

Releases leave every Tuesday. A change merged by Monday 18:00 ships the next day.

1. Tag the release candidate.
2. Run the smoke suite against staging.
3. Promote to production.

## On-call

| Week | Primary | Secondary |
|------|---------|-----------|
| 40 | Lan | Minh |
| 41 | Minh | Thang |

Pages go to the primary first. The secondary takes over after 15 minutes.

```bash
oncall swap --week 41 --with thang
```

> Escalate to the engineering manager when an incident lasts longer than one hour.

![Escalation flow](escalation.png)
