import { useEffect, useMemo, useRef, useState } from "react";
import {
  baselineReplays,
  buildStages,
  describeAction,
  describeNow,
  formatSeconds,
  latestAttemptReplays,
  modelUse,
  REPLAYS_REQUIRED,
  sandboxesDestroyed,
  VERDICTS,
  type Stage,
  type Task,
  type TaskEvent,
} from "./stages";

// Ticket, page and model text is untrusted. It is only ever rendered as React text nodes.

export function Run({ taskId, onNew }: { taskId: string; onNew: () => void }) {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [task, setTask] = useState<Task | null>(null);
  const [missing, setMissing] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [pinned, setPinned] = useState<number | null>(null);

  useEffect(() => {
    let closed = false;
    const refresh = () =>
      fetch(`/api/tasks/${taskId}`).then(async (response) => {
        if (closed) return;
        if (response.status === 404) setMissing(true);
        else if (response.ok) setTask(await response.json());
      });
    refresh();
    const stream = new EventSource(`/api/tasks/${taskId}/events`);
    stream.onmessage = (message) => {
      const event: TaskEvent = JSON.parse(message.data);
      setEvents((current) => (current.some((e) => e.seq === event.seq) ? current : [...current, event]));
      if (["status_changed", "issue_filed", "outcome", "patch_verified", "pull_request_opened"].includes(event.type)) refresh();
      if (event.type === "status_changed" && event.text === "done") stream.close();
    };
    return () => {
      closed = true;
      stream.close();
    };
  }, [taskId]);

  const status = task?.status ?? "queued";
  const finished = status === "done";
  const waiting = status === "awaiting_approval";

  useEffect(() => {
    if (finished || waiting) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [finished, waiting]);

  // Once the evidence exists, it is the source of truth for how many sandboxes ran.
  const [recorded, setRecorded] = useState<{ total: number; destroyed: number } | null>(null);
  useEffect(() => {
    if (!finished && !waiting) return;
    fetch(`/api/tasks/${taskId}/manifest`)
      .then((response) => (response.ok ? response.json() : null))
      .then((manifest) => {
        if (!manifest) return;
        const sandboxes: { destroyed: boolean }[] = manifest.sandboxes ?? [];
        setRecorded({ total: sandboxes.length, destroyed: sandboxes.filter((s) => s.destroyed).length });
      })
      .catch(() => setRecorded(null));
  }, [taskId, finished, waiting]);

  const stages = useMemo(() => buildStages(task, events, now), [task, events, now]);
  const steps = events.filter((e) => e.type === "browser_action");
  const baseline = baselineReplays(events);
  const patched = latestAttemptReplays(events);
  const frozen = events.find((e) => e.type === "repro_frozen");
  const started = events[0] ? Date.parse(events[0].at) : null;
  const ended = finished || waiting ? Date.parse(events[events.length - 1]?.at ?? "") : now;

  if (missing) {
    return (
      <main className="verdict verdict-FAILED">
        <h1>There’s no run with this link</h1>
        <p>It may be mistyped. You can report a new bug instead.</p>
        <button className="primary" onClick={onNew}>Report a bug</button>
      </main>
    );
  }

  const verdict = (finished || waiting) && task ? VERDICTS[task.outcome] : null;
  const now_ = describeNow(status, events);
  const lastShot = pinned ?? [...steps].reverse().find((s) => s.data?.screenshot_sha256)?.seq ?? null;
  const shown = steps.find((s) => s.seq === lastShot);
  const failingShot = baseline.map((e) => e.data?.screenshot_sha256).filter(Boolean).pop();
  const passingShot = patched.map((e) => e.data?.screenshot_sha256).filter(Boolean).pop();
  const failingUrl = baseline.map((e) => e.data?.final_url).filter(Boolean).pop();
  const passingUrl = patched.map((e) => e.data?.final_url).filter(Boolean).pop();
  const lastThought = [...events].reverse().find((e) => e.type === "agent_thought" && !e.text?.startsWith("Reading source") && !e.text?.startsWith("Running the regression"))?.text;

  return (
    <main className="run">
      {verdict && task ? (
        <section className={`verdict verdict-${task.outcome}`}>
          <div className="verdict-words">
            <h1>{verdict.title}</h1>
            <p>{verdict.detail}</p>
            {task.outcome === "FAILED" && task.outcome_rule && <p className="reason">Reason: {task.outcome_rule}</p>}
            <div className="links">
              {task.pull_request_url && <a className="strong-link" href={task.pull_request_url} rel="noreferrer noopener" target="_blank">Open the pull request</a>}
              {task.issue_url && <a href={task.issue_url} rel="noreferrer noopener" target="_blank">Read the filed issue</a>}
              <a href={`/api/tasks/${taskId}/manifest`} rel="noreferrer noopener" target="_blank">See the evidence</a>
              {finished && <button onClick={onNew}>Report another bug</button>}
            </div>
          </div>
          <div className="stamp" aria-hidden="true">{verdict.stamp}</div>
        </section>
      ) : (
        <section className="now" role="status" aria-live="polite">
          <span className="pulse" aria-hidden="true" />
          <div>
            <h1>{now_.title}</h1>
            <p>{now_.detail}</p>
          </div>
          {started && <span className="elapsed">{formatSeconds((ended - started) / 1000)}</span>}
        </section>
      )}

      <div className="case">
        <aside className="rail">
          <h2 className="rail-title">Progress</h2>
          <ol className="stages">
            {stages.map((stage) => <StageRow key={stage.id} stage={stage} />)}
          </ol>
          <Proof events={events} taskId={taskId} recorded={recorded} />
        </aside>

        <div className="work">
          {task?.ticket && (
            <section className="slip ticket">
              <h2>What the customer said</h2>
              <blockquote>{task.ticket}</blockquote>
            </section>
          )}

          {waiting && task && <Decision taskId={taskId} onDecided={() => window.location.reload()} />}

          {failingShot && passingShot ? (
            <section className="block">
              <h2>Same script, before and after</h2>
              <div className="compare">
                <Shot title="Before the fix: the bug appears" tone="before" url={failingUrl} src={`/api/tasks/${taskId}/artifacts/${String(failingShot)}`} alt="The final screen when the frozen script runs on the current code" />
                <Shot title={task?.outcome === "NO_VERIFIED_FIX" ? "With the last fix tried" : "After the fix: same steps, bug gone"} tone={patched.at(-1)?.data?.exit_code === 0 ? "after" : "before"} url={passingUrl} src={`/api/tasks/${taskId}/artifacts/${String(passingShot)}`} alt="The final screen when the same frozen script runs on the patched code" />
              </div>
            </section>
          ) : null}

          {task?.diff && <Change diff={task.diff} explanation={task.explanation} />}

          {frozen && (
            <section className="block">
              <h2>The frozen script, run in fresh sandboxes</h2>
              <p className="block-note">Code wrote this script from the agent’s steps, and hashed it before any fix existed.</p>
              <Replays label="On the current code, the bug must appear" events={baseline} want="bug" done={finished || waiting || status !== "confirming"} />
              {events.some((e) => e.type === "patch_proposed") && (
                <Replays label="On the fixed code, the bug must be gone" events={patched} want="clean" done={finished || waiting} />
              )}
              <p className="fine hash">Script SHA-256: {String(frozen.data?.script_sha256)}</p>
            </section>
          )}

          <section className="block">
            <h2>What the agent did in the browser</h2>
            <div className="session">
              <div className="browser">
                <div className="address">{shown ? String(shown.data?.url ?? "").replace("http://127.0.0.1:9090", "") || "/" : "Starting the sandbox"}</div>
                {shown?.data?.screenshot_sha256 ? (
                  <a href={`/api/tasks/${taskId}/artifacts/${String(shown.data.screenshot_sha256)}`} target="_blank" rel="noreferrer noopener" title="Open the full-size screenshot">
                    <img src={`/api/tasks/${taskId}/artifacts/${String(shown.data.screenshot_sha256)}`} alt={`The sandboxed browser after this step: ${describeAction(shown.text)}`} />
                  </a>
                ) : (
                  <div className="browser-empty">The browser appears here once the sandbox has passed its checks.</div>
                )}
              </div>
              <div className="steps">
                <ol>
                  {steps.map((step) => (
                    <li key={step.seq}>
                      <button
                        className={`step ${step.seq === lastShot ? "step-shown" : ""} ${step.data?.ok === false ? "step-failed" : ""}`}
                        aria-pressed={step.seq === lastShot}
                        onClick={() => setPinned(step.seq)}
                      >
                        {describeAction(step.text)}
                        {step.data?.ok === false && " (this step failed)"}
                      </button>
                    </li>
                  ))}
                </ol>
                {pinned !== null && !finished && !waiting && <button className="quiet" onClick={() => setPinned(null)}>Follow the live view</button>}
                {lastThought && <p className="thought">{lastThought}</p>}
              </div>
            </div>
          </section>

          <Attempts events={events} />
        </div>
      </div>
    </main>
  );
}

function StageRow({ stage }: { stage: Stage }) {
  const marks: Record<Stage["state"], string> = { done: "Done", active: "In progress", upcoming: "Not started", stopped: "Stopped here", skipped: "Not needed" };
  return (
    <li className={`stage stage-${stage.state}`} aria-current={stage.state === "active" ? "step" : undefined}>
      <span className="stage-mark" aria-hidden="true" />
      <div>
        <strong>{stage.name}</strong>
        <span className="stage-note">
          <span className="visually-hidden">{marks[stage.state]}. </span>
          {[stage.note, stage.seconds !== null && stage.state !== "upcoming" ? formatSeconds(stage.seconds) : ""].filter(Boolean).join(", ")}
        </span>
      </div>
    </li>
  );
}

function Proof({ events, taskId, recorded }: { events: TaskEvent[]; taskId: string; recorded: { total: number; destroyed: number } | null }) {
  const created = events.find((e) => e.type === "sandbox_created");
  const facts = created?.data?.attestation as Record<string, unknown> | undefined;
  const use = modelUse(events);
  const live = sandboxesDestroyed(events);
  const counts = recorded ? { destroyed: recorded.destroyed, created: recorded.total } : live;
  if (!facts) return null;
  return (
    <section className="proof">
      <h2 className="rail-title">Read back from Docker</h2>
      <dl>
        <div><dt>Runtime</dt><dd>{facts.runtime === "runsc" ? "gVisor" : String(facts.runtime)}</dd></div>
        <div><dt>Network</dt><dd>{facts.network_mode === "none" ? "None" : String(facts.network_mode)}</dd></div>
        <div><dt>Disk</dt><dd>{facts.read_only ? "Read-only" : "Writable"}</dd></div>
        <div><dt>Memory</dt><dd>{Number(facts.memory_bytes) / 1024 ** 3} GiB</dd></div>
        <div><dt>Processes</dt><dd>{String(facts.pids_limit)} at most</dd></div>
        <div><dt>Sandboxes destroyed</dt><dd>{counts.destroyed} of {counts.created}</dd></div>
      </dl>
      {use.calls > 0 && (
        <>
          <h2 className="rail-title">Vultr Serverless Inference</h2>
          <dl>
            <div><dt>Model</dt><dd>{use.models.join(", ")}</dd></div>
            <div><dt>Calls</dt><dd>{use.calls}</dd></div>
            <div><dt>Tokens</dt><dd>{use.tokens.toLocaleString("en")}</dd></div>
          </dl>
        </>
      )}
      <a href={`/api/tasks/${taskId}/manifest`} rel="noreferrer noopener" target="_blank">See the full evidence</a>
    </section>
  );
}

function Shot({ title, tone, url, src, alt }: { title: string; tone: "before" | "after"; url: unknown; src: string; alt: string }) {
  return (
    <figure className={`shot shot-${tone}`}>
      <figcaption>{title}</figcaption>
      {url ? <div className="address">{String(url).replace("http://127.0.0.1:9090", "")}</div> : null}
      <a href={src} target="_blank" rel="noreferrer noopener" title="Open the full-size screenshot"><img src={src} alt={alt} /></a>
    </figure>
  );
}

function Change({ diff, explanation }: { diff: string; explanation: string | null }) {
  const lines = diff.split("\n");
  const file = lines.find((line) => line.startsWith("+++ "))?.replace("+++ b/", "");
  return (
    <section className="block">
      <h2>The proposed change</h2>
      {explanation && <p className="explain">{explanation}</p>}
      {file && <p className="file">{file}</p>}
      <pre tabIndex={0} aria-label="The proposed code change">
        {lines
          .filter((line) => !line.startsWith("--- ") && !line.startsWith("+++ "))
          .map((line, index) => {
            const kind = line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : line.startsWith("@@") ? "hunk" : undefined;
            return <span key={index} className={kind}>{line + "\n"}</span>;
          })}
      </pre>
      <p className="block-note">The model wrote the explanation, so treat it as a claim. The code above is what would change.</p>
    </section>
  );
}

function Replays({ label, events, want, done }: { label: string; events: TaskEvent[]; want: "bug" | "clean"; done: boolean }) {
  return (
    <div className="replay-group">
      <h3>{label}</h3>
      <div className="replays">
        {Array.from({ length: REPLAYS_REQUIRED }, (_, index) => {
          const replay = events[index];
          const bug = replay?.data?.symptom_observed === true;
          const clean = replay?.data?.exit_code === 0;
          const failed = Boolean(replay) && !bug && !clean;
          const asWanted = want === "bug" ? bug : clean;
          const running = !replay && !done && index === events.length;
          return (
            <div key={index} className={`replay ${replay ? (asWanted ? "replay-pass" : "replay-fail") : running ? "replay-running" : "replay-empty"}`}>
              <strong>Run {index + 1}</strong>
              <span>
                {replay
                  ? bug ? "Bug appeared" : clean ? "Bug gone" : failed ? "A step failed" : ""
                  : running ? "Running now" : done ? "Not needed" : "Waiting"}
              </span>
              {replay?.data?.destroyed === true && <span className="fine">Sandbox destroyed</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Attempts({ events }: { events: TaskEvent[] }) {
  const proposals = events.filter((e) => e.type === "patch_proposed");
  const results = events.filter((e) => e.type === "patch_verified" || (e.type === "policy_checked" && e.data?.passed === false));
  if (proposals.length < 2 && !results.some((r) => r.data?.outcome !== "FIX_VERIFIED")) return null;
  return (
    <section className="block">
      <h2>Fix attempts</h2>
      <ol className="attempts">
        {proposals.map((proposal, index) => {
          const result = results.find((r) => r.data?.attempt === proposal.data?.attempt);
          return (
            <li key={proposal.seq}>
              <strong>Attempt {String(proposal.data?.attempt)}</strong> changed {((proposal.data?.files as string[]) ?? []).join(", ")}.{" "}
              {result?.text ?? (index === proposals.length - 1 ? "Being tested now." : "")}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Decision({ taskId, onDecided }: { taskId: string; onDecided: () => void }) {
  // Read from the box at click time, so a password manager or autofill can't leave this out of date.
  const passcodeBox = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function decide(decision: "approve" | "reject") {
    const passcode = passcodeBox.current?.value.trim() ?? "";
    if (!passcode) {
      setError("Enter the operator passcode first.");
      passcodeBox.current?.focus();
      return;
    }
    setSending(true);
    setError(null);
    try {
      const response = await fetch(`/api/tasks/${taskId}/decision`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, passcode, note: note || undefined }),
      });
      if (response.ok) {
        onDecided();
        return;
      }
      setError(response.status === 403 ? "That passcode is wrong. Only the operator can decide." : "This fix isn’t waiting for a decision any more. Reload the page.");
    } catch {
      setError("The server didn’t answer. Check your connection and try again.");
    }
    setSending(false);
  }

  return (
    <form className="decision" onSubmit={(event) => event.preventDefault()}>
      <h2>Your decision</h2>
      <p>A passing script shows this symptom is gone. It doesn’t show the change is good, so read it below first. Visitors can watch, but only the operator can decide.</p>
      <div className="fields">
        <div>
          <label htmlFor="passcode">Operator passcode</label>
          <input id="passcode" ref={passcodeBox} type="password" autoComplete="off" maxLength={200} />
        </div>
        <div>
          <label htmlFor="note">Note (optional)</label>
          <input id="note" maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} />
        </div>
      </div>
      <div className="actions">
        <button className="primary" type="button" disabled={sending} onClick={() => decide("approve")}>Approve and open pull request</button>
        <button type="button" disabled={sending} onClick={() => decide("reject")}>Reject this fix</button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
    </form>
  );
}
