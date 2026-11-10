// Turns the raw event stream into plain words. No outcome is decided here: the page only reports
// what the server already recorded. Event text is untrusted, and is only ever rendered as text.

export type Task = {
  id: string;
  kind?: string;
  status: string;
  outcome: string;
  outcome_rule: string | null;
  issue_url: string | null;
  pull_request_url: string | null;
  diff: string | null;
  explanation: string | null;
  ticket?: string;
  created_at?: string;
};

export type TaskEvent = {
  seq: number;
  at: string;
  type: string;
  text?: string;
  data?: Record<string, unknown>;
};

export type StageState = "done" | "active" | "upcoming" | "stopped" | "skipped";

export type Stage = {
  id: string;
  name: string;
  state: StageState;
  note: string;
  seconds: number | null;
};

const STAGES = [
  { id: "reproduce", name: "Reproduce the bug", statuses: ["queued", "exploring"] },
  { id: "confirm", name: "Confirm it three times", statuses: ["confirming"] },
  { id: "file", name: "File the bug", statuses: ["filing"] },
  { id: "fix", name: "Write a fix", statuses: ["patching"] },
  { id: "test", name: "Test the fix", statuses: ["verifying"] },
  { id: "decide", name: "A developer decides", statuses: ["awaiting_approval", "publishing"] },
];

export const REPLAYS_REQUIRED = 3;

/** One action in plain words, such as "Clicked the link to /bookmarks?q=#python". */
export function describeAction(text: string | undefined): string {
  if (!text) return "";
  const match = text.match(/^(goto|click|fill|press|select|wait_for|screenshot|read_text)(?: (.*))?$/s);
  if (!match) return text;
  const [, action, rest = ""] = match;
  const target = describeTarget(rest);
  switch (action) {
    case "goto":
      return `Opened ${decode(rest)}`;
    case "click":
      return `Clicked ${target}`;
    case "fill": {
      const split = splitSelector(rest);
      return `Typed “${split.value}” into ${describeTarget(split.selector)}`;
    }
    case "press": {
      const split = splitSelector(rest);
      return `Pressed ${split.value} in ${describeTarget(split.selector)}`;
    }
    case "select": {
      const split = splitSelector(rest);
      return `Chose “${split.value}” in ${describeTarget(split.selector)}`;
    }
    case "wait_for":
      return `Waited for ${target}`;
    case "screenshot":
      return "Took a screenshot";
    default:
      return "Read the page";
  }
}

function decode(value: string): string {
  try {
    return decodeURI(value).replace(/%23/g, "#");
  } catch {
    return value;
  }
}

function describeTarget(selector: string): string {
  const link = selector.match(/^a\[href="([^"]*)"\]$/);
  if (link) return `the link to ${decode(link[1])}`;
  const text = selector.match(/^\w+:has-text\("([^"]*)"\)$/);
  if (text) return `“${text[1]}”`;
  const named = selector.match(/^\w+\[name="([^"]*)"\]$/);
  if (named) return `the ${named[1]} field`;
  const id = selector.match(/^#([\w-]+)$/);
  if (id) return `the ${id[1]} field`;
  return selector;
}

/** Selectors can hold spaces only inside quotes or brackets, so find the first space outside them. */
function splitSelector(rest: string): { selector: string; value: string } {
  let depth = 0;
  let quoted = false;
  for (let i = 0; i < rest.length; i++) {
    const c = rest[i];
    if (c === '"') quoted = !quoted;
    else if (!quoted && (c === "[" || c === "(")) depth++;
    else if (!quoted && (c === "]" || c === ")")) depth--;
    else if (!quoted && depth === 0 && c === " ") return { selector: rest.slice(0, i), value: rest.slice(i + 1) };
  }
  return { selector: rest, value: "" };
}

/** Only the current fix attempt counts, because earlier attempts were already rejected. */
export function latestAttemptReplays(events: TaskEvent[]): TaskEvent[] {
  const lastProposal = events.map((e) => e.type).lastIndexOf("patch_proposed");
  return events
    .slice(lastProposal + 1)
    .filter((e) => e.type === "replay_finished" && e.data?.stage === "patched");
}

export function baselineReplays(events: TaskEvent[]): TaskEvent[] {
  return events.filter((e) => e.type === "replay_finished" && e.data?.stage !== "patched");
}

function attemptCount(events: TaskEvent[]): number {
  return events.filter((e) => e.type === "patch_proposed").length;
}

/** How long each stage ran, added up across fix attempts. */
function stageSeconds(events: TaskEvent[], now: number, finished: boolean): Record<string, number> {
  const changes = events.filter((e) => e.type === "status_changed");
  const totals: Record<string, number> = {};
  changes.forEach((change, index) => {
    const stage = STAGES.find((s) => s.statuses.includes(change.text ?? ""));
    if (!stage) return;
    const next = changes[index + 1];
    if (!next && finished) return;
    const end = next ? Date.parse(next.at) : now;
    totals[stage.id] = (totals[stage.id] ?? 0) + Math.max(0, (end - Date.parse(change.at)) / 1000);
  });
  return totals;
}

export function buildStages(task: Task | null, events: TaskEvent[], now: number = Date.now()): Stage[] {
  const status = task?.status ?? "queued";
  const outcome = task?.outcome ?? "PENDING";
  const finished = status === "done";
  const seconds = stageSeconds(events, now, finished);
  const reached = new Set(
    events.filter((e) => e.type === "status_changed").flatMap((e) => {
      const stage = STAGES.find((s) => s.statuses.includes(e.text ?? ""));
      return stage ? [stage.id] : [];
    }),
  );
  const currentIndex = STAGES.findIndex((s) => s.statuses.includes(status));
  const lastReached = STAGES.reduce((last, stage, index) => (reached.has(stage.id) ? index : last), 0);

  return STAGES.map((stage, index) => {
    let state: StageState;
    if (!finished) {
      state = index < currentIndex ? "done" : index === currentIndex ? "active" : "upcoming";
    } else if (index < lastReached) {
      state = "done";
    } else if (index > lastReached) {
      state = "skipped";
    } else {
      state = ["APPROVED", "REJECTED", "REPRODUCED"].includes(outcome) ? "done" : "stopped";
    }
    return { ...stage, state, note: stageNote(stage.id, state, task, events), seconds: seconds[stage.id] ?? null };
  });
}

function stageNote(id: string, state: StageState, task: Task | null, events: TaskEvent[]): string {
  if (state === "upcoming" || state === "skipped") return "";
  const steps = events.filter((e) => e.type === "browser_action").length - 1;
  switch (id) {
    case "reproduce": {
      if (task?.outcome === "NOT_REPRODUCED" && state === "stopped") return "No bug seen";
      return steps > 0 ? `${steps} ${steps === 1 ? "action" : "actions"}` : "";
    }
    case "confirm": {
      const replays = baselineReplays(events);
      const shown = replays.filter((e) => e.data?.symptom_observed === true).length;
      return replays.length ? `Bug appeared in ${shown} of ${replays.length}` : "";
    }
    case "file": {
      const number = task?.issue_url?.split("/").pop();
      return number ? `Issue ${number} filed` : "";
    }
    case "fix": {
      const attempts = attemptCount(events);
      return attempts ? `Attempt ${attempts} of 3` : "";
    }
    case "test": {
      const replays = latestAttemptReplays(events);
      const passed = replays.filter((e) => e.data?.exit_code === 0).length;
      if (state === "stopped") return "No fix passed";
      return replays.length ? `Passed ${passed} of ${replays.length}` : "";
    }
    case "decide": {
      if (task?.outcome === "APPROVED") return "Approved";
      if (task?.outcome === "REJECTED") return "Rejected";
      return task?.status === "publishing" ? "Recording the decision" : "Waiting for a person";
    }
    default:
      return "";
  }
}

export type NowMessage = { title: string; detail: string };

/** What is happening this moment, and why it takes the time it takes. */
export function describeNow(status: string, events: TaskEvent[]): NowMessage {
  const lastThought = [...events].reverse().find((e) => e.type === "agent_thought")?.text;
  switch (status) {
    case "queued":
      return { title: "Waiting for a free sandbox", detail: "Tickets run one at a time, so this one starts when the current run finishes." };
    case "exploring":
      if (!events.some((e) => e.type === "sandbox_created")) {
        return {
          title: "Starting a disposable sandbox",
          detail: "Copying the app, creating a throwaway test user, and checking every limit with Docker. This takes about 20 seconds.",
        };
      }
      return { title: "The agent is trying to reproduce the bug", detail: lastThought ?? "It reads the page and chooses one action at a time. Code checks each action first." };
    case "confirming": {
      const done = baselineReplays(events).length;
      return {
        title: events.some((e) => e.type === "repro_frozen") ? `Replaying the frozen script: run ${Math.min(done + 1, REPLAYS_REQUIRED)} of ${REPLAYS_REQUIRED}` : "Freezing the steps into a script",
        detail: "Each run gets a brand-new sandbox, and takes about 30 seconds. The bug must appear every time, or nothing gets filed.",
      };
    }
    case "filing":
      return { title: "Filing the bug on GitHub", detail: "The issue gets the customer’s words, a screenshot and the exact script. It goes to our own fork only." };
    case "patching": {
      const attempt = attemptCount(events) + 1;
      return { title: `Writing a fix: attempt ${Math.min(attempt, 3)} of 3`, detail: lastThought?.startsWith("Reading source") ? lastThought : "The model reads the source and proposes exact text edits." };
    }
    case "verifying": {
      const tail = events.slice(events.map((e) => e.type).lastIndexOf("patch_proposed") + 1);
      if (tail.some((e) => e.type === "agent_thought" && e.text?.startsWith("Running the regression tests"))) {
        return { title: "Running existing tests, with and without the fix", detail: "142 tests, to catch anything the fix breaks. This is the slow part, and takes a few minutes." };
      }
      const done = latestAttemptReplays(events).length;
      return { title: `Replaying the same script on the fixed code: run ${Math.min(done + 1, REPLAYS_REQUIRED)} of ${REPLAYS_REQUIRED}`, detail: "It’s the script frozen before the fix was written, so the fix can’t change its own test." };
    }
    case "publishing":
      return { title: "Recording the decision", detail: "If the fix was approved, this opens the pull request on our fork." };
    default:
      return { title: "Working", detail: "" };
  }
}

export type Verdict = { stamp: string; title: string; detail: string };

export const VERDICTS: Record<string, Verdict> = {
  NOT_REPRODUCED: {
    stamp: "Not reproduced",
    title: "The bug didn’t appear",
    detail: "Nothing was filed, because the agent couldn’t show a bug that a script could repeat. Try describing the steps in more detail.",
  },
  REPRODUCED: { stamp: "Reproduced", title: "The bug is real, and it’s filed", detail: "The frozen script showed the bug in every run. The fix phase is switched off." },
  FIX_VERIFIED: {
    stamp: "Fix verified",
    title: "A fix passed. Now a person decides",
    detail: "The same script shows the bug on the current code and passes on the fix. Nothing opens until a developer approves.",
  },
  NO_VERIFIED_FIX: {
    stamp: "No verified fix",
    title: "The bug is filed, but no fix passed",
    detail: "All three attempts were rejected, so the issue stands for a developer to pick up. Nothing else was opened.",
  },
  APPROVED: { stamp: "Approved", title: "Approved, and the pull request is open", detail: "A developer read the change and approved it. The pull request is on our own fork." },
  REJECTED: { stamp: "Rejected", title: "A developer rejected the fix", detail: "No pull request was opened. The filed issue still stands." },
  FAILED: { stamp: "Run failed", title: "This run didn’t finish", detail: "Sandboxes are destroyed on failure too. The reason is below. Try again in a few minutes." },
};

export function formatSeconds(seconds: number): string {
  const whole = Math.round(seconds);
  if (whole < 1) return "under 1 s";
  if (whole < 60) return `${whole} s`;
  return `${Math.floor(whole / 60)} min ${String(whole % 60).padStart(2, "0")} s`;
}

export function modelUse(events: TaskEvent[]): { calls: number; tokens: number; models: string[]; cost: number | null } {
  const calls = events.filter((e) => e.type === "llm_call");
  return {
    calls: calls.length,
    tokens: calls.reduce((sum, e) => sum + Number(e.data?.prompt_tokens ?? 0) + Number(e.data?.completion_tokens ?? 0), 0),
    models: [...new Set(calls.map((e) => String(e.data?.model ?? "")).filter(Boolean))],
    // Runs from before cost tracking have no figure, so the total is unknown, not zero.
    cost: calls.length && calls.every((e) => typeof e.data?.cost_usd === "number")
      ? calls.reduce((sum, e) => sum + Number(e.data?.cost_usd), 0)
      : null,
  };
}

export function sandboxesDestroyed(events: TaskEvent[]): { created: number; destroyed: number } {
  const replays = events.filter((e) => e.type === "replay_finished");
  const session = events.filter((e) => e.type === "sandbox_destroyed");
  return {
    created: events.filter((e) => e.type === "sandbox_created").length + replays.length,
    destroyed: replays.filter((e) => e.data?.destroyed === true).length + session.filter((e) => e.data?.destroyed === true).length,
  };
}
