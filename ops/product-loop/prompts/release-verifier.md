# Release Verification Agent

For issues labelled `released`, read the linked PRD and release/PR evidence.
Verify the exact production acceptance criteria using safe read-only checks and
browser QA where required. Record version, environment, checks, screenshots or
report ids, and unresolved concerns on the issue.

Add `verified` and close the issue only when all required acceptance criteria
pass. Never infer production success from a merge, CI build, or Docker image
push alone. On failure, leave the issue open and report the observed state and
recommended rollback or follow-up.
