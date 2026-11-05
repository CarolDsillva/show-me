# Demo script

Target about 3:30, and the limit is 5 minutes and 300MB. Record the real public URL with the address bar visible. A full run takes about 6 minutes, so cut the waits and mark each cut on screen, such as "3 replays, 90 seconds, sped up". Never change a result.

## Before recording

1. Run `bash scripts/deploy.sh`, then `bash scripts/smoke.sh`. All 16 checks must pass.
2. Use the "Tag count link goes nowhere" sample. It verified on every run since the patch-step fixes, but that's only 3 runs, so do a full dry run first.
3. Tickets are limited to 10 an hour from one address. Don't use them up rehearsing.
4. Have the operator passcode ready, and keep it out of the recording. Use a password box only.
5. Open three tabs: the site, the fork's issues, and the attack page.

## Script

| Time | Show | Say |
|---|---|---|
| 0:00 | Front page | "Support gets a ticket that says 'it's broken'. Engineering can't reproduce it, so it bounces for days. What if that ticket arrived with proof?" |
| 0:20 | Click the sample, then "Reproduce this bug" | "This is one customer's words. It runs against linkding, a real open-source app, with a real past bug put back in." |
| 0:35 | Live view: the blue "happening now" bar, progress on the left, browser and steps | "An agent on Vultr Serverless Inference drives real Chromium, in a disposable gVisor sandbox on a Vultr VM with no network. It reads the page and picks one action at a time, and code checks each action." |
| 1:05 | Replays filling in | Mark the cut. "Now code freezes those steps into a script and hashes it. It replays in three fresh sandboxes, and must show the bug every time. The model's opinion doesn't count." |
| 1:20 | The filed issue on GitHub | "That's a real issue, with the customer's words, a screenshot and the exact script." |
| 1:35 | Fix verified, before and after | Mark the cut. "The model proposed this one-line change. The same frozen script failed on the current code three times, and passes on the patch three times. Existing tests still pass. A patch can't cheat by changing its own test." |
| 2:05 | The diff, then scroll up to "Your decision" and click Approve | "But a passing script doesn't mean the patch is good, so a person decides. Nothing has been opened yet." Click Approve. |
| 2:20 | The pull request on the fork | "Now there's a real pull request, on our own fork only." |
| 2:35 | Attack page, host panel | "Now the hard part. What if the AI's code is hostile? These are real attacks, each in a fresh sandbox." |
| 2:45 | Click "Run all 7 attacks", which takes about 40 seconds. Mark any cut | "The secret hunt finds only a planted decoy. The network is unreachable. The kernel refuses the fork bomb, and the loop is killed from outside at 15 seconds." |
| 3:10 | "7 of 7 contained", open one set of checks, host panel steady | "These results are measured from the host, not reported by the sandbox. The API kept responding, the host's file hash is unchanged, and no sandboxes are left. We also remove each protection in testing, to prove these checks can fail." |
| 3:25 | Architecture slide | "One Vultr VM runs the control plane. The API holds the keys but can't reach Docker. `sandboxd` can reach Docker but holds no secrets." |
| 3:40 | Honest limits slide | "What this doesn't prove: one symptom gone isn't a good patch. Two of our three demo bugs got verified fixes on every repeat run, and the third never did. The system says so and leaves the issue standing. It's a small sample." |
| 3:55 | Close | "Next: helpdesk integration, and any repository. A vague ticket becomes a proven patch, and a person stays in charge." |

## Don't say

- "Escape-proof", "the bug is fixed", "the patch is safe" or "tamper-proof".
- "Found a bug". These are real past bugs, recreated by reverting their fixes.
- Any reliability percentage. The sample is too small.

## If it goes wrong live

If a run fails, say what the page says. `NO_VERIFIED_FIX` and `FAILED` are honest outcomes. For a recording, retake it, and mention nothing was edited apart from marked cuts.
