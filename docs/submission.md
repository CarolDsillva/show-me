# lablab.ai submission text

Draft. Check it against the actual form, which takes priority. Nothing here has been submitted.

## Title (50 characters at most)

ReproFix: from a customer's words to a proven fix

## Short summary (255 characters at most)

A customer describes a bug. An agent reproduces it in a real browser in a disposable Vultr sandbox, files it with evidence, writes a patch, and proves it with the same frozen script. Nothing ships until a developer approves.

## Long description

"It's broken" tickets bounce between support and engineering for days, because nobody can reproduce the problem. ReproFix starts where that handoff starts: a customer's own words.

An agent running on Vultr Serverless Inference explores the real app in Chromium, inside a disposable gVisor sandbox with no network. When it sees the bug, code freezes its steps into a hashed Playwright script and replays it in three fresh sandboxes. Only then does a bug report go to GitHub, with the steps, a screenshot and the script. The model then proposes a patch. The same frozen script must pass on the patched code three times, with no new test failures. A developer sees the failing run, the passing run and the diff, and a pull request opens only when they approve.

Code decides every outcome, and the model only explains it. A patch can't pass by changing its own test, because the script was frozen first and tests are off limits to the patch policy.

Containment is something you can try. The public site has seven attack buttons: a secret hunt, network escape, deleting everything, filling the disk, a fork bomb, a memory bomb and an infinite loop. Each runs in a fresh sandbox and is judged from outside it, using evidence like the kernel's own count of refused forks. Our security suite also removes each protection in turn, to prove the checks fail when they should.

We say plainly what this doesn't prove. A passing replay shows one symptom is gone, not that the patch is good. The demo bugs are real past bugs in linkding, recreated by reverting their fixes. On our latest repeat runs, two of the three got a verified fix every time. The third never did, and the system says so, leaving the filed issue standing.

Beyond the hackathon, it could connect to helpdesks such as Zendesk, accept any repository with a recipe for running it, and move sandboxes to a separate worker VM, so that a support team's vague tickets arrive at engineering with proof attached.

## Tags

Check these against `lablab.ai/tech`: Vultr, Serverless Inference, LangGraph, gVisor, Docker, Playwright, FastAPI, AI agents, Customer support.

## Demo platform

Vultr Cloud Compute VM

## Application URL

https://137-220-32-178.sslip.io

## Still needed

- GitHub repository URL. It must be public, and the user creates it
- Video URL or file
- Slides as PDF
- Cover image, 16:9
- Which challenge to enter, once the organizers answer
