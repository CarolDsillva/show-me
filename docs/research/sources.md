# Research sources

Read on 2026-11-03. Event pages take priority over the general rule book. Recheck anything marked unconfirmed before relying on it.

## Hackathon

| Source | Used for |
|---|---|
| [lablab.ai event page](https://lablab.ai/ai-hackathons/vultr-hackathon) | Challenge rules, required stack, submission, judging, credits |
| [Vultr Community event page](https://community.vultr.com/e/vultr-agent-rush-hackathon) | Containment focus areas, deliverables, examples of containment moments |
| [lablab rule book](https://lablab.ai/hackathon-rules) | Originality scoring, grace period, disqualification, formats |
| [lablab guidelines](https://lablab.ai/ai-articles/hackathon-guidelines) | Form limits, video limits |

## Vultr

| Source | Used for |
|---|---|
| [Live model list](https://api.vultrinference.com/v1/models) | Model IDs, capabilities, prices. Checked directly: 35 models |
| [Inference provisioning](https://docs.vultr.com/products/serverless/inference/provisioning) | Console steps |
| [Tool calling guide](https://docs.vultr.com/how-to-use-tool-calling-with-vultr-serverless-inference) | Message shapes. Its example model is not in the live list |
| [Node.js inference guide](https://docs.vultr.com/how-to-use-vultr-serverless-inference-in-nodejs) | Base URL and auth. Model examples are stale, and its models path differs from the event page |
| [Agent sandboxing guide](https://docs.vultr.com/how-to-set-up-agent-sandboxing-on-vultr-cloud-compute) | Microsandbox evaluation |
| [VX1 product page](https://www.vultr.com/products/vx1-compute/) | Nested virtualization claim. Marketing only, not tested |
| [Vultr API](https://www.vultr.com/api/) | Instance and firewall fields |
| [Firewall group management](https://docs.vultr.com/products/network/firewall-groups/management/groups) | Firewall setup |
| [vultr-cli instance reference](https://docs.vultr.com/reference/vultr-cli/instance) | CLI overview. Exact flags unconfirmed |

## Sandbox and agent tooling

| Source | Used for |
|---|---|
| [gVisor install](https://gvisor.dev/docs/user_guide/install/) | apt install steps |
| [gVisor Docker quick start](https://gvisor.dev/docs/user_guide/quick_start/docker/) | Runtime setup, debug and strace flags, and the warning that `dmesg` isn't a security check |
| [Docker daemon access](https://docs.docker.com/engine/security/protect-access/) | Daemon access amounts to root on the host |
| [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) | Resume semantics and idempotency |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/durable-execution) | Checkpointers, `thread_id`, durable savers |

## Other public entries, reviewed for originality only

Don't copy code. Plagiarism means disqualification. Summaries came from each README, which wasn't independently checked.

| Repository | Summary |
|---|---|
| [airlock](https://github.com/YodHeVauHe/airlock) | Hardened Docker, policy engine, hash-chained receipts, "Torture Chamber" |
| [cage](https://github.com/Pawnnwap/cage) | `sandboxd` holds the socket, eight drills, signed evidence packs |
| [vultr-agent-rush](https://github.com/Zhiyilang/vultr-agent-rush) | CSV analyst, hash-chained audit log, containment endpoint |

## Not found

- Exact submission deadline
- Judging criteria weights
- Serverless Inference subscription price, included tokens, and rate limits. `vultr.com/pricing` refused automated fetch, and older pages conflict. Check the console
- Rules on pre-existing code. Build everything during the event window, and ask on Discord if in doubt
- Vultr metadata documentation: the URL tried returned 404. Vultr's address is believed to be `169.254.169.254`, confirm on the VM

## Added on 2026-11-04

| Source | Used for |
|---|---|
| [linkding](https://github.com/sissbruecker/linkding) | Target app: MIT license, Django and SQLite, Playwright tests, real fixes such as PR #1452 |
| [Live Vultr plans](https://api.vultr.com/v2/plans) | `vc2-4c-8gb` at $40 a month, available in `sea` |
| [Vultr port 25 policy](https://docs.vultr.com/support/products/compute/why-is-smtp-blocked) | Outbound email is blocked by default. Show Me sends no email |
