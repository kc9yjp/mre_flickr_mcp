# /pr-review

Check for open pull requests on the current repo, select one to review, perform a thorough code review, and post the review as a GitHub PR review.

## Workflow

1. Run `gh pr list` to find open PRs. If there are multiple, ask the user which one to review. If there is exactly one, proceed with it. If there are none, report that and stop.

2. Check out the PR branch, build, and run tests:
   ```bash
   gh pr checkout <number>
   source .venv/bin/activate && pip install -r requirements-dev.txt -q
   pytest --tb=short -q
   ```
   Record whether the install succeeded and whether tests passed, failed, or errored. Note any test failures — they are blocking issues.

   Then build the Docker image so the user can do manual testing:
   ```bash
   docker compose build
   ```
   Report whether the build succeeded or failed. If it succeeds, tell the user they can start the container with `docker compose up -d` and test manually before you post the review. Wait for the user to confirm they are done testing before proceeding.

   After review is complete and posted, return to the original branch:
   ```bash
   git checkout -
   ```

3. Fetch the full diff and metadata:
   ```bash
   gh pr view <number>
   gh pr diff <number>
   ```

4. Read every changed file in full — don't rely on diff context alone. Use the `Read` tool for any file where the diff is hard to evaluate without surrounding context.

5. Review across these dimensions (check all that apply to the changes):

   **Correctness**
   - Logic errors, off-by-one, unhandled edge cases
   - Race conditions, concurrency issues
   - Incorrect assumptions about external API behavior

   **Security**
   - Injection risks: SQL, shell, template, path traversal
   - Authentication/authorization gaps
   - Secrets or credentials in code or logs
   - Unvalidated user input crossing a trust boundary
   - CSRF, XSS, open redirect

   **Code quality**
   - Clear naming and intent
   - Dead code, unreachable branches, leftover debug statements
   - Unnecessary complexity or abstraction
   - Duplicated logic that should be shared

   **Robustness**
   - Missing error handling at system boundaries (external APIs, file I/O, DB)
   - Overly broad exception catches that swallow real errors
   - Resource leaks (connections, file handles, locks)

   **Documentation and observability**
   - Public interfaces missing docstrings where they add value
   - Non-obvious behavior without a comment explaining *why*
   - Missing or misleading log messages for operational visibility

   **Tests**
   - New behavior covered by tests
   - Tests that would pass even if the code is wrong (vacuous assertions)
   - Existing tests broken by the change

6. Compose a review:
   - **Summary**: 2-3 sentences on what the PR does and overall assessment.
   - **Build & Tests**: two lines — pip+pytest result, then Docker build result.
   - **Issues**: numbered list; each item names the file:line, describes the problem, explains why it matters, and gives a concrete fix or suggestion.
   - **Nits**: small style or clarity items that don't block approval (optional section, keep short).
   - **Verdict**: one of APPROVE, REQUEST_CHANGES, or COMMENT with a one-line rationale.

7. Show the review to the user and ask for confirmation before posting.

8. Post the review using:
   ```bash
   gh pr review <number> --<approve|request-changes|comment> --body "<review text>"
   ```

## Notes
- Do not approve a PR with failing tests, unresolved security issues, or correctness bugs.
- If `pip install`, `pytest`, or `docker compose build` itself errors, report that as a blocking issue.
- A Docker build failure is blocking — do not approve a PR that can't be built.
- Flag "already in pool" or similar benign operational errors as informational, not blocking.
- If the PR description is missing or thin, note it as a nit — good PR descriptions are part of good practice.
- Prioritize: security > correctness > robustness > code quality > documentation > nits.
- Always restore the original branch with `git checkout -` after checkout, even if tests fail.
