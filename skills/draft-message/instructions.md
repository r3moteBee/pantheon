# Draft Message

Write professional messages with appropriate tone and structure.

**Voice first.** If the message goes out under the user's name, call `writing_voice` first (the voice they name, or the default one) and follow its notes and samples. If they have no voices, `writing_voice(action="list")` says so; carry on with the tone below.

## Procedure

1. **Understand the context.** What kind of message? Who's the audience? What's the purpose — inform, request, follow up, escalate, celebrate? If the user didn't specify, infer from context or ask.

2. **Set the tone.** Match the tone to the situation:
   - **formal** — External clients, executives, legal matters. Full sentences, no contractions, proper salutations.
   - **professional** (default) — Colleagues, stakeholders. Clear and direct, friendly but businesslike.
   - **casual** — Team chat, close colleagues. Conversational, contractions fine, can use light humor.
   - **friendly** — Informal updates, congratulations, team morale. Warm, personal, encouraging.

3. **Structure by message type:**

   **Email:**
   - Subject line (concise, specific, front-loaded with the key point)
   - Greeting
   - Purpose in the first sentence — don't bury the lead
   - Context/details in the body
   - Clear ask or next step
   - Sign-off

   **Slack/Chat:**
   - Lead with the point — no "Hi, I wanted to reach out about..."
   - Keep it scannable — short paragraphs, one idea per message
   - Use formatting sparingly (bold for emphasis, not decoration)

   **Announcement:**
   - Headline: what's happening
   - Why it matters to the audience
   - What they need to do (if anything)
   - Where to find more info or ask questions

4. **Review for common pitfalls:**
   - Too long? Cut by 30%.
   - Passive voice hiding responsibility? ("Mistakes were made" → "We made a mistake")
   - Vague ask? ("Let me know your thoughts" → "Can you approve by Friday?")
   - Missing context that the recipient needs?

5. **Present the draft** and offer to adjust tone, length, or emphasis.

## Important

- If recalled memory has context about the recipient or topic, use it to personalize.
- Never be sycophantic — "I hope this email finds you well" adds nothing.
- Match the user's voice: their writing voice when they have one (`writing_voice`), otherwise how they write to you. If they write casually, don't draft something stiff.
- For sensitive messages (bad news, escalations, performance feedback), err on the side of directness with empathy. Don't soften the message to the point where the recipient misses the point.
