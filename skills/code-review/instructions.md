# Code Review

Review code for bugs, security issues, performance problems, and style.

## Procedure

1. **Get the code.** In this order:
   - Code pasted in the conversation — review that.
   - A file the user attached to the chat — it is an artifact under `chat-attachments/`: `read_artifact`.
   - A path — an artifact the project produced (`read_artifact`; `list_artifacts` to find it), a workspace or repo-checkout file (`read_file`), or a file in the bound GitHub repo (`github`).
   - A whole folder or project: list it first, then review the files that matter (entry points, anything handling input, auth or money) rather than everything.
   If no code and no path was given, ask which file to review.

2. **Understand the context.** What does this code do? What language and framework is it? Read the neighbouring files and any README or conventions document when the project is in the workspace, an artifact folder or a bound repo, so the review follows the project's own conventions rather than generic ones.

3. **Review systematically.** Check each category in order of severity:

   **Bugs & Logic Errors** (Critical)
   - Off-by-one errors, null/undefined access, race conditions
   - Incorrect logic flow, missing edge cases
   - Unreachable code, infinite loops

   **Security Issues** (Critical)
   - Injection vulnerabilities (SQL, command, XSS)
   - Hardcoded secrets or credentials
   - Missing input validation or sanitization
   - Insecure cryptographic practices
   - Path traversal, SSRF

   **Performance** (Important)
   - Unnecessary allocations or copies in hot paths
   - N+1 queries, missing indexes
   - Blocking operations in async code
   - Unbounded memory growth

   **Style & Maintainability** (Advisory)
   - Unclear naming, missing type hints
   - Functions doing too many things
   - Dead code, commented-out blocks
   - Missing error handling

4. **Verify what you can.** If a builder is configured (`run_build`) and the code is a package or project in the artifacts, run its tests or a compile before judging; a failing test is a finding with proof. Don't claim you ran something you didn't.

5. **Structure the response:**
   - Lead with a 1-2 sentence overall assessment.
   - Group findings by severity: critical, important, advisory.
   - For each finding: quote the specific line(s), explain the issue, suggest a fix.
   - End with what the code does well — good reviews aren't only about problems.

6. **Offer the next step.** The user may want the fixes applied (`update_artifact` for an artifact, the repo tools for a bound repo — in a branch, never straight to main) or the review kept (`save_to_artifact` under `reviews/<file>.md`). Don't change the code unasked.

## Important

- Be specific. "This might have issues" is useless. Point to exact lines.
- Suggest fixes, don't just point out problems.
- Calibrate tone — this is a review, not an attack. Acknowledge good patterns.
- If the user asked to focus on a specific area (security, performance, etc.), prioritize that but still note any critical issues in other areas.
- Don't flag things you are unsure about as bugs; say what you would need to check. A library's behaviour you don't know is a question, not a finding.
