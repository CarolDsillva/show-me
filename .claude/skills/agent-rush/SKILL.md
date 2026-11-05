---
name: agent-rush
description: Use when checking any feature, scope decision or claim against the Vultr Agent Rush hackathon rules, Blast Radius Zero requirements, judging criteria or required stack.
---

# Agent Rush rules

Facts below come from the event pages, read on 2026-11-03. Sources are in `docs/research/sources.md`. The event page and submission form take priority over the general lablab rule book, so recheck them if something matters. Rules can change.

## Event

- Online build 3-8 November 2026. On-site phase 8 November at Pierpont Place, Salt Lake City. Travel isn't covered.
- Teams of 1 to 6. Enrollment on lablab.ai and joining the lablab Discord are required.
- Only lablab.ai submissions are judged. A Vultr Community post is optional and doesn't replace it.
- Submission deadline: **unconfirmed**. Check the event page. The rule book gives a five-minute grace period after the deadline.
- Each participant gets $200 of Vultr credit, on an account with an email not previously used at Vultr. No Vultr GPUs at this event.
- Must be original and MIT-compliant. Plagiarism means immediate disqualification.

## Which challenge

ReproFix fits Challenge 2, Powering the Future of Work (customer support), and is built to meet Challenge 1's stricter rules too. Challenge 2 requires a Vultr VM backend and public web app. Serverless Inference is optional there, but we use it for everything to keep both open. Whether one project can enter both, and whether one prize list covers both, is **unconfirmed**. Ask on Discord.

## Challenge 1: Blast Radius Zero

Build a web-based agent that does real work, either writing and running code or operating a real browser, with every action inside an isolated sandbox on Vultr. It plans a task, dispatches it, and returns verifiable output. Executed results are required, not descriptions of what would happen.

| Required | Rule |
|---|---|
| Vultr VM | Backend, as the central control and orchestration layer, not just static hosting |
| Web app on Vultr | Public URL with a product-style experience |
| Vultr Serverless Inference | All agent LLM calls. OpenAI-compatible, base URL `https://api.vultrinference.com/v1` |
| Isolated sandbox | Containers or throwaway instances on Vultr, never inside the app process. Docker strongly recommended |

### The four containment areas

| Area | Rule | Our implementation |
|---|---|---|
| Process isolation | Execution never touches the host or app runtime | `sandboxd`, gVisor, `--network=none`, app and browser in one container |
| Secret hygiene | No API keys or credentials in the sandbox | Env allowlist, no key in `sandboxd`, canary tests |
| Resource limits | Time and memory caps on every run | Server-side profile, attested from the host |
| Lifecycle discipline | Reset or destroy after each task | One container per run, destruction verified, reaper |

### Required deliverables

- Public GitHub repository with setup documentation
- Backend deployed on a Vultr VM, with LLM calls through Serverless Inference
- Public demo URL
- Recorded video with **at least one containment moment**, such as absorbing `rm -rf`, an infinite loop or a hostile page
- Architecture and use-case explanation

## Judging

Four criteria, with no weights published. The rule book scores 1 to 5.

| Criterion | What earns points here |
|---|---|
| Application of Technology | How effectively models are integrated. Show browser exploration, patch writing and real token accounting. The demo should show every feature |
| Presentation | Problem, solution and value clearly communicated in under 5 minutes |
| Business Value | Practical impact: support and engineering stop bouncing vague bugs |
| Originality | Novel elements. A copy of existing solutions scores 1. Use customer language in, frozen-script proof out, and developer approval |

## Checking a decision

Before accepting a feature or shortcut, ask:

1. Does an LLM call bypass Vultr Serverless Inference? Reject.
2. Could any untrusted code run outside the sandbox, even as a fallback? Reject.
3. Is the VM only serving files, while real logic runs elsewhere? Reject.
4. Would a judge see executed results, or a description? Require executed results.
5. Does it improve a judging criterion within the time left? If not, cut it.

## Common mistakes

| Mistake | Instead |
|---|---|
| Trusting a model name from a Vultr guide | Check live `GET /v1/models`. `kimi-k2-instruct` in the tool-calling guide isn't listed |
| Using `/v1/chat/models` from older guides | Use `/v1/models`, the path linked from the event page |
| Offline mock so a demo "works" without keys | Banned outside unit tests. It would misrepresent the product |
| Gemini as a backup model | Banned. It would break Challenge 1 eligibility |
| Presenting a reverted past bug as a new discovery | Say what it is |
| Subprocess fallback when Docker is missing | Banned. Fail closed |
| Describing the system as unbreakable | State the boundaries and residual risks |
