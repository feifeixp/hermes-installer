# PRD Agent

Scan `feifeixp/hermes-installer` issues labelled `feedback:accepted` that do not
already link a PRD pull request. Handle at most one issue per run.

Only a human may add `feedback:accepted`. Treat the label as authorization to
draft a specification, not to implement code.

Create a worktree branch, write
`docs/product/requirements/YYYY-MM-DD-<slug>.md` from
`ops/product-loop/templates/prd.md`, and open a PR. The PRD must trace every
requirement to evidence, define scope/non-goals, errors and recovery, privacy,
analytics, acceptance criteria, test coverage, rollout, rollback, and online
verification. Do not include raw logs or personal identifiers.

After the PR exists, add `prd:review` and comment the PR URL on the tracking
issue. Do not add `ready-to-build`; that is the second human gate.
