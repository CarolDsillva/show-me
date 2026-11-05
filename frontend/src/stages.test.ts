import { describe, expect, it } from "vitest";
import { buildStages, describeAction, describeNow, formatSeconds, latestAttemptReplays, modelUse, type Task, type TaskEvent } from "./stages";

let seq = 0;
function event(type: string, text?: string, data?: Record<string, unknown>, second = seq): TaskEvent {
  seq += 1;
  return { seq, at: new Date(2026, 10, 5, 10, 0, second).toISOString(), type, text, data };
}

function task(status: string, outcome = "PENDING", extra: Partial<Task> = {}): Task {
  return { id: "t", status, outcome, outcome_rule: null, issue_url: null, pull_request_url: null, diff: null, explanation: null, ...extra };
}

const statuses = (...names: string[]) => names.map((name, i) => event("status_changed", name, undefined, i * 10));
const states = (t: Task, events: TaskEvent[]) => Object.fromEntries(buildStages(t, events, Date.parse(events[0].at) + 500000).map((s) => [s.id, s.state]));

describe("describeAction", () => {
  it.each([
    ["goto /tags", "Opened /tags"],
    ['click a[href="/bookmarks?q=%23python"]', "Clicked the link to /bookmarks?q=#python"],
    ['click button:has-text("Search")', "Clicked “Search”"],
    ["fill #search py", "Typed “py” into the search field"],
    ['fill input[name="q"] two words', "Typed “two words” into the q field"],
    ['press input[name="q"] Enter', "Pressed Enter in the q field"],
    ["screenshot", "Took a screenshot"],
    ["read_text", "Read the page"],
    ["Logged in with a throwaway test user", "Logged in with a throwaway test user"],
  ])("%s", (raw, expected) => {
    expect(describeAction(raw)).toBe(expected);
  });

  it("leaves an unusual selector readable", () => {
    expect(describeAction("click .btn.delete")).toBe("Clicked .btn.delete");
  });
});

describe("buildStages", () => {
  it("marks the stage in progress, with earlier ones done", () => {
    const events = statuses("queued", "exploring", "confirming");
    expect(states(task("confirming"), events)).toMatchObject({ reproduce: "done", confirm: "active", file: "upcoming", decide: "upcoming" });
  });

  it("stops at reproduce when no bug was seen, and skips the rest", () => {
    const events = statuses("queued", "exploring", "done");
    expect(states(task("done", "NOT_REPRODUCED"), events)).toMatchObject({ reproduce: "stopped", confirm: "skipped", decide: "skipped" });
  });

  it("stops at confirm when the replays didn't show the bug", () => {
    const events = statuses("queued", "exploring", "confirming", "done");
    expect(states(task("done", "NOT_REPRODUCED"), events)).toMatchObject({ reproduce: "done", confirm: "stopped", file: "skipped" });
  });

  it("stops at testing when no fix was verified", () => {
    const events = statuses("queued", "exploring", "confirming", "filing", "patching", "verifying", "patching", "verifying", "done");
    expect(states(task("done", "NO_VERIFIED_FIX"), events)).toMatchObject({ fix: "done", test: "stopped", decide: "skipped" });
  });

  it("waits at decide, then completes on approval or rejection", () => {
    const events = statuses("queued", "exploring", "confirming", "filing", "patching", "verifying", "awaiting_approval");
    expect(states(task("awaiting_approval", "FIX_VERIFIED"), events).decide).toBe("active");
    const after = [...events, ...statuses("publishing", "done")];
    expect(states(task("done", "APPROVED"), after).decide).toBe("done");
    expect(states(task("done", "REJECTED"), after).decide).toBe("done");
  });

  it("marks where a failed run stopped", () => {
    const events = statuses("queued", "exploring", "confirming", "filing", "done");
    expect(states(task("done", "FAILED"), events)).toMatchObject({ confirm: "done", file: "stopped", fix: "skipped" });
  });

  it("adds up time across repeated fix attempts", () => {
    const events = [
      event("status_changed", "patching", undefined, 0),
      event("status_changed", "verifying", undefined, 10),
      event("status_changed", "patching", undefined, 30),
      event("status_changed", "verifying", undefined, 35),
      event("status_changed", "done", undefined, 45),
    ];
    const stages = buildStages(task("done", "NO_VERIFIED_FIX"), events);
    expect(stages.find((s) => s.id === "fix")?.seconds).toBe(15);
    expect(stages.find((s) => s.id === "test")?.seconds).toBe(30);
  });
});

describe("describeNow", () => {
  it("explains the wait for the sandbox, then follows the agent", () => {
    expect(describeNow("exploring", []).title).toBe("Starting a disposable sandbox");
    const events = [event("sandbox_created"), event("agent_thought", "Open the Tags page")];
    expect(describeNow("exploring", events).detail).toBe("Open the Tags page");
  });

  it("counts confirm runs", () => {
    const events = [event("repro_frozen"), event("replay_finished", "x", { stage: "baseline" })];
    expect(describeNow("confirming", events).title).toContain("run 2 of 3");
  });

  it("names the slow regression step, and only for the current attempt", () => {
    const earlier = [event("patch_proposed"), event("agent_thought", "Running the regression tests with and without the patch")];
    expect(describeNow("verifying", earlier).title).toContain("existing tests");
    expect(describeNow("verifying", [...earlier, event("patch_proposed")]).title).toContain("run 1 of 3");
  });
});

it("counts only the current attempt's replays", () => {
  const events = [
    event("patch_proposed"),
    event("replay_finished", "a", { stage: "patched", exit_code: 42 }),
    event("patch_proposed"),
    event("replay_finished", "b", { stage: "patched", exit_code: 0 }),
  ];
  expect(latestAttemptReplays(events).map((e) => e.text)).toEqual(["b"]);
});

it("adds up model use", () => {
  const events = [event("llm_call", "m", { model: "m", prompt_tokens: 5, completion_tokens: 2 }), event("llm_call", "m", { model: "m", prompt_tokens: 1, completion_tokens: 1 })];
  expect(modelUse(events)).toEqual({ calls: 2, tokens: 9, models: ["m"] });
});

it("formats durations", () => {
  expect(formatSeconds(0.3)).toBe("under 1 s");
  expect(formatSeconds(9.4)).toBe("9 s");
  expect(formatSeconds(125)).toBe("2 min 05 s");
});
