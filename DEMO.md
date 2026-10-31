# Live demo script (about 3 minutes)

**Before going on stage:** `python scripts/seed.py`, restart the server, open http://localhost:8000, and click **＋ New session** on both panes.
Send one throwaway message first ("hi") to warm up the model, then reset again.

Screen layout: **Alice's agent | Bob's agent | Team Memory (live)**.

Every line below is one click away: press **💡** next to a chat box (or pick one from the empty chat) to fill it in, then press Enter.

---

### 0. Hook (10 s)
> "Every AI agent on your team starts from zero. Bob's agent has no idea what Alice's agent learned an hour ago. TeamBrain is a shared memory, stored in MongoDB, that every agent reads and writes, and it corrects itself, expires things, and keeps private things private."

### 1. Create memories (Alice)
Type in **Alice**:
```
Staging DB is down for the next 2 minutes. Also, we decided to use pnpm, not npm. The payments API is rate limited to 100 requests per second, and there's a freeze on new dependencies starting Wednesday.
```
Point at:
- The green "saved" chips under the answer (types: constraint / decision).
- The **memory panel**: new cards slide in **live**, pushed by a **MongoDB change stream** (no polling).
- The staging card has a **⏳ countdown**. That's the **TTL index**. Leave it and come back later.

### 2. Memory changes behaviour (Bob, new session)
Click **＋ New session** on Bob ("fresh context, no chat history"). Type in **Bob**:
```
Write a quick script that installs a charting library and bulk-syncs 10,000 orders to the payments API.
```
Bob's agent uses **pnpm**, **throttles to 100 req/s**, warns about the **dependency freeze**, and retries 429s with backoff (a seeded strategy). Citations show as **[M1]** chips.

**The contrast:** flip Bob's toggle to **Memory OFF**, send the same message again, and you get generic `npm install` with no throttling. Flip it back ON.

### 3. Explain why (inspector)
Click **🔍 Why these memories?** under Bob's answer:
- **Atlas `$vectorSearch`** with a **pre-filter** (workspace, status=active, scope/owner). Privacy is enforced *inside the query*.
- Re-rank formula: similarity, recency (per-type half-life) and importance.
- ✓ marks which ones the model actually cited.

Click a citation chip to show the **memory inspector**: source quote, audit log, raw MongoDB document.

### 4. Hard cases (rapid fire)
**Correction:** in **Alice**:
```
Update: the payments API rate limit was raised to 500 requests per second.
```
The old card is struck through as **superseded** and the new one is **v2**. Open it to show the version chain. Ask Bob again ("what rate limit should my sync script use?") and he gets **500**.

**Conflict:** in **Bob**:
```
We use npm on this project.
```
The agent does **not** overwrite Alice's decision. It shows **⚠️ conflict** and asks Bob to confirm. (Optional: `Yes, confirmed, override it, we switched to npm.` to supersede it with confirmation.)

**Private + secrets:** in **Alice**:
```
I prefer very terse answers, bullet points only.
```
The memory is saved as 🔒 **private**. Switch "View as" to Bob and it disappears. Then:
```
Here's my AWS key so you can deploy: AKIAIOSFODNN7EXAMPLE
```
The key is redacted **before** it reaches the LLM or the database, and nothing is stored.

**Forget:** in **Alice**:
```
Forget the dependency freeze, it was cancelled.
```
The card shows as **deleted**: soft-deleted, with an audit event, and purged later by the TTL index. The inspector also has **Erase permanently** (right to be forgotten).

**Expiry:** by now the staging-DB card has expired, and a toast says "expired via TTL index".
> "MongoDB's TTL monitor runs about every 60 s, so recall also filters `expireAt > now`. An expired memory is never served, even before it's physically deleted."

**Feedback (if time):** click 👎 on any answer and type "don't add comments to code". It's stored as a private feedback memory and changes the next answer.

### 5. Close (20 s)
Show the architecture slide and the eval number (`python scripts/eval.py`: recall@k, privacy violations = 0).
> "One document per memory, holding the vector, owner, type, source and lifecycle. `$vectorSearch` with pre-filters, TTL for expiry, change streams for multi-agent sync, and an append-only audit log. Any agent can plug in through our MCP server."

**(Bonus)** In a terminal, run Claude Code with the TeamBrain MCP server (README) and ask "add a script that calls the payments API". It recalls pnpm and the 500 req/s limit by itself.
