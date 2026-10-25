# Explain Code

Explain what code does in plain language, adapted to the user's level.

## Procedure

1. **Get the code.** In this order:
   - Code pasted in the conversation — use it as is.
   - A file the user attached to the chat — it is an artifact under `chat-attachments/`: `read_artifact`.
   - A path — an artifact the project produced (`read_artifact`), a workspace or repo-checkout file (`read_file`), or a file in the bound GitHub repo (`github`).
   - An error message or stack trace — explain that; read the file it names if you can.
   If neither code nor a path was given, ask what to explain.

2. **Gauge the audience.** Use context clues to set the right level:
   - **beginner** — Assume no programming background. Explain what variables, functions, and loops are. Use analogies.
   - **intermediate** (default) — Assume basic programming knowledge. Focus on what the code does, not language syntax.
   - **expert** — Skip the basics. Focus on design decisions, trade-offs, and subtle behavior (concurrency, memory, edge cases).

3. **Start with the big picture.** Before diving into details, explain what the code accomplishes in 1-2 sentences. What problem does it solve?

4. **Walk through the logic.** Explain in the order a reader would encounter the code:
   - For functions/classes: purpose, inputs, outputs, side effects.
   - For complex logic: trace through with a concrete example.
   - For configuration: what each setting controls and sensible defaults.

5. **Highlight the interesting parts.** Don't explain every line — focus on:
   - Non-obvious logic or clever tricks
   - Potential gotchas or easy-to-miss behavior
   - Connections to other parts of the codebase (read the neighbouring files when the project is bound or in the workspace, rather than guessing)

6. **Offer to go deeper.** End with something like "Want me to explain [specific part] in more detail?" if there are complex sections you glossed over.

## Important

- Use the user's language. If they asked "what does this do" casually, don't respond with a formal technical document.
- When explaining errors: explain what went wrong, why, and how to fix it. If the fix is small, show the corrected lines.
- Reference line numbers when explaining specific parts of longer files.
- If the code has bugs or issues, mention them naturally as part of the explanation — don't ignore them. For a full review, the `/code-review` skill is the right tool.
- Explain only what the code says. If a library call's behaviour matters and you are not certain of it, say so or look it up (`web_fetch` on its documentation) rather than assert it.
