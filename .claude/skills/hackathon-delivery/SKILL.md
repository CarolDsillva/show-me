---
name: hackathon-delivery
description: Use when planning the Agent Rush demo, video, slides, README or lablab.ai submission, cutting scope against the deadline, or checking whether the entry is ready to submit.
---

# Hackathon delivery

An unsubmitted product scores zero. Stay submittable from the end of Day 4, and cut scope before cutting quality or containment.

## Scope cuts, in order

1. P2 stretch items
2. P1: syscall telemetry, vision verification, extra bugs
3. UI polish beyond clarity
4. Number of sample bugs
5. The fix phase, falling back to ReproBot

Never cut: real execution, the containment rules, Vultr Inference, the public URL, the containment moment, or the submission materials. Don't call a sample bug a discovery: say it's a real past bug, recreated by reverting its fix.

## Judge-ready demo script

Target about 3:30, hard limit under 5 minutes. Record against the real public URL, showing the address bar. No faked or sped-up results without saying so on screen.

| Time | Show | Say (in substance) |
|---|---|---|
| 0:00 | Problem | "It's broken" tickets bounce between support and engineering for days |
| 0:20 | Type a ticket | One plain sentence from a customer. A judge can reword it |
| 0:35 | Live browser session | The agent explores a real app in a throwaway sandbox on Vultr |
| 1:10 | Issue filed | Real issue with steps, screenshots and a frozen script |
| 1:30 | Patch proposed | Code writes the rules, and the model writes the patch |
| 1:50 | **Side by side** | Same frozen script: fails on current code, passes on the patch, three times each |
| 2:15 | **Developer approves** | Nothing happens until a person clicks Approve, and then a real pull request opens on our fork |
| 2:35 | **Containment drill** | Secret hunt finds only decoys. Network fails. `rm -rf` hits read-only. Fork bomb, memory and loop all stop at their limits |
| 3:05 | Host health panel | Heartbeat never stopped. Host hash and disk unchanged. Zero containers remain |
| 3:20 | Architecture | Vultr VM control plane, Serverless Inference, `sandboxd`, gVisor |
| 3:35 | Value and honesty | What this proves, what it doesn't, and where it goes next |

Keep the final script at `docs/demo-script.md`.

### Live demo safeguards

- Deploy, smoke test and dry run shortly before recording.
- Have a pinned demo bug, and test it three times in a row first.
- Long waits may be cut in the edit, if marked on screen. Don't change results.
- Keep the safety recording from Day 4.
- For on-site, have the video ready in case of a network failure, and say that it's a recording.

## Materials

| Item | Rule |
|---|---|
| Title | Max 50 characters |
| Short summary | Max 255 characters |
| Long description | At least 100 words, include how it could scale beyond the hackathon |
| Tags | Technology and category. Use names on `lablab.ai/tech` |
| Cover image | 16:9 |
| Video | MP4 or MOV, under 5 minutes, under 300MB, must show a containment moment |
| Slides | PDF: problem, solution, demo, architecture, containment, business value, roadmap |
| GitHub | Public, setup docs, MIT license |
| Demo platform | State "Vultr Cloud Compute VM" |
| Application URL | Public, working during judging |

Check the actual form, since it takes priority over these notes.

## README required contents

The event page requires the first three.

1. Vultr services and infrastructure used, and for what
2. Backend and deployment architecture, with a diagram
3. The agent's use case, and how it executes real tasks
4. Containment: each of the four focus areas, how it's enforced, and how to verify it
5. Setup from a clean VM, with commands that have been tested
6. How to run tests, and latest real results
7. Residual risks and limits, stated openly
8. Public demo URL, video link and license

Multiple repositories, if any, must be described in the README. `demo/hostile/` needs a clear note saying its payloads are for sandbox testing only. Credit linkding and keep its license, and explain that the demo fork recreates past bugs.

## Scoring moves

| Criterion | Make sure judges see |
|---|---|
| Application of Technology | Models driving a real browser and writing patches, with real token accounting and every feature shown in the video |
| Presentation | One clear story: a complaint becomes a proven patch, and a person stays in charge |
| Business Value | Named users and a real handoff: support to engineering. How it could grow: helpdesk integration, any repository |
| Originality | Customer language in, before-and-after proof out, using a frozen script the patch can't alter |

## Final checklist

Run this from a clean browser, not logged in, before submitting.

- [ ] The exact deadline and time zone were confirmed from an official source
- [ ] The public URL works, and a real ticket completes
- [ ] The containment drill works on the public URL
- [ ] Approval opens a real pull request on our fork, and nothing reached upstream
- [ ] All LLM calls are confirmed to be on Vultr Inference, from the manifest
- [ ] Security suite and evals passed on the deployed commit, with results recorded
- [ ] The repository has been scanned for secrets, then made public, with an MIT license
- [ ] README meets the list above, and setup was tested on a clean VM
- [ ] Video plays, meets the limits, and shows the containment moment
- [ ] Slides are exported as PDF
- [ ] All form fields are within limits, and the entry shows as submitted on lablab.ai
- [ ] The VM will stay up through judging, and credit is enough
- [ ] No claim overstates what the evidence shows

If a technical problem blocks submission, contact the organizers **before** the deadline. Only they can extend it, and there's only a five-minute grace period.

Actual submission, making the repository public, and publishing videos are done by the user, or with the user's explicit approval.
