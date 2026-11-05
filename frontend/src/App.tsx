import { useEffect, useMemo, useState } from "react";

// Ticket, page and model text is untrusted. It is only ever rendered as React text nodes.

type Sample = { id: string; title: string; text: string; hostile?: boolean };

type Task = {
  id: string;
  status: string;
  outcome: string;
  outcome_rule: string | null;
  issue_url: string | null;
};

type TaskEvent = {
  seq: number;
  at: string;
  type: string;
  text?: string;
  data?: Record<string, unknown>;
};

const STATUS_WORDS: Record<string, string> = {
  queued: "Waiting for a free sandbox",
  exploring: "Trying to reproduce the bug in a real browser",
  confirming: "Replaying the frozen steps in fresh sandboxes",
  filing: "Filing the bug",
  done: "Finished",
};

const OUTCOME_WORDS: Record<string, string> = {
  REPRODUCED: "Bug reproduced",
  NOT_REPRODUCED: "Not reproduced",
  FAILED: "This run failed",
};

function taskFromUrl(): string | null {
  const value = new URLSearchParams(window.location.search).get("task");
  return value && /^[a-f0-9]{32}$/.test(value) ? value : null;
}

export function App() {
  const [taskId, setTaskId] = useState<string | null>(taskFromUrl);

  useEffect(() => {
    const onPop = () => setTaskId(taskFromUrl());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function open(id: string | null) {
    window.history.pushState(null, "", id ? `?task=${id}` : window.location.pathname);
    setTaskId(id);
  }

  return (
    <div className="page">
      <header className="masthead">
        <a
          className="wordmark"
          href={window.location.pathname}
          onClick={(event) => {
            event.preventDefault();
            open(null);
          }}
        >
          ReproFix
        </a>
        <p>From a customer's words to a bug a developer can trust.</p>
      </header>
      {taskId ? <Run taskId={taskId} onNew={() => open(null)} /> : <Submit onStarted={open} />}
    </div>
  );
}

function Submit({ onStarted }: { onStarted: (id: string) => void }) {
  const [text, setText] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    fetch("/api/samples")
      .then((response) => (response.ok ? response.json() : []))
      .then(setSamples)
      .catch(() => setSamples([]));
  }, []);

  async function send() {
    setSending(true);
    setError(null);
    try {
      const response = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (response.status === 202) {
        onStarted((await response.json()).id);
        return;
      }
      if (response.status === 429) {
        const body = await response.json();
        setError(body.detail);
      } else {
        setError("Write the problem in 10 to 2,000 characters, then try again.");
      }
    } catch {
      setError("The server didn't answer. Check your connection and try again.");
    }
    setSending(false);
  }

  return (
    <main className="submit">
      <section className="slip">
        <label htmlFor="ticket">What went wrong in linkding?</label>
        <textarea
          id="ticket"
          rows={6}
          maxLength={2000}
          value={text}
          placeholder="Describe it the way a customer would."
          onChange={(event) => setText(event.target.value)}
        />
        <div className="slip-actions">
          <button className="primary" disabled={sending || text.trim().length < 10} onClick={send}>
            {sending ? "Sending ticket" : "Reproduce this bug"}
          </button>
          {error && <p role="alert" className="error">{error}</p>}
        </div>
      </section>
      <aside className="samples">
        <h2>Or start from a sample</h2>
        {samples.map((sample) => (
          <button key={sample.id} className="sample" onClick={() => setText(sample.text)}>
            {sample.title}
          </button>
        ))}
        <p className="fine">
          Runs on a real copy of linkding with a real past bug put back in. Each run uses a disposable
          sandbox with no network, and the sandbox is destroyed afterwards.
        </p>
      </aside>
    </main>
  );
}

function Run({ taskId, onNew }: { taskId: string; onNew: () => void }) {
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [task, setTask] = useState<Task | null>(null);
  const [ticket, setTicket] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    let closed = false;
    const refresh = () =>
      fetch(`/api/tasks/${taskId}`).then(async (response) => {
        if (closed) return;
        if (response.status === 404) setMissing(true);
        else if (response.ok) {
          const body = await response.json();
          setTask(body);
          setTicket(body.ticket ?? null);
        }
      });
    refresh();
    const stream = new EventSource(`/api/tasks/${taskId}/events`);
    stream.onmessage = (message) => {
      const event: TaskEvent = JSON.parse(message.data);
      setEvents((current) => (current.some((e) => e.seq === event.seq) ? current : [...current, event]));
      if (event.type === "status_changed" || event.type === "issue_filed" || event.type === "outcome") refresh();
      if (event.type === "status_changed" && event.text === "done") stream.close();
    };
    return () => {
      closed = true;
      stream.close();
    };
  }, [taskId]);

  const screenshot = useMemo(() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const data = events[i].data;
      if (events[i].type === "browser_action" && data?.screenshot_sha256) {
        return { sha: String(data.screenshot_sha256), url: String(data.url ?? "") };
      }
    }
    return null;
  }, [events]);

  const steps = events.filter((e) => e.type === "browser_action");
  const thoughts = events.filter((e) => e.type === "agent_thought");
  const replays = events.filter((e) => e.type === "replay_finished");
  const destroyed = events.find((e) => e.type === "sandbox_destroyed");
  const frozen = events.find((e) => e.type === "repro_frozen");
  const done = task?.status === "done";

  if (missing) {
    return (
      <main className="verdict verdict-FAILED">
        <h1>There's no run with this link</h1>
        <button className="primary" onClick={onNew}>Report a bug</button>
      </main>
    );
  }

  return (
    <main className="run">
      {done && task ? (
        <section className={`verdict verdict-${task.outcome}`}>
          <h1>{OUTCOME_WORDS[task.outcome] ?? task.outcome}</h1>
          <p>{task.outcome_rule}</p>
          <div className="verdict-links">
            {task.issue_url && <a href={task.issue_url} rel="noreferrer noopener" target="_blank">Read the filed issue</a>}
            <a href={`/api/tasks/${taskId}/manifest`} target="_blank" rel="noreferrer noopener">See the evidence manifest</a>
            <button onClick={onNew}>Report another bug</button>
          </div>
        </section>
      ) : (
        <p className="now" role="status">{STATUS_WORDS[task?.status ?? "queued"] ?? task?.status}</p>
      )}

      <div className="bench">
        <section className="browser" aria-label="What the agent's browser shows">
          <div className="address">{screenshot?.url || "Starting the sandbox"}</div>
          {screenshot ? (
            <img src={`/api/tasks/${taskId}/artifacts/${screenshot.sha}`} alt="The latest view from the sandboxed browser" />
          ) : (
            <div className="browser-empty">The browser appears here once the sandbox has passed its checks.</div>
          )}
        </section>

        <section className="trail">
          {ticket && <blockquote className="slip quote">{ticket}</blockquote>}
          <h2>What the agent did</h2>
          <ol>
            {steps.map((step) => (
              <li key={step.seq} className={step.data?.ok === false ? "failed" : undefined}>
                {step.text}
              </li>
            ))}
          </ol>
          {thoughts.length > 0 && <p className="thought">{thoughts[thoughts.length - 1].text}</p>}
          {destroyed && (
            <p className="fine">
              Exploration sandbox {destroyed.data?.destroyed ? "destroyed and confirmed gone" : "not confirmed destroyed"}.
            </p>
          )}
        </section>
      </div>

      {frozen && (
        <section className="replays">
          <h2>Same frozen steps, three fresh sandboxes</h2>
          <div className="replay-row">
            {[0, 1, 2].map((index) => {
              const replay = replays[index];
              const symptom = replay?.data?.symptom_observed === true;
              return (
                <div key={index} className={`replay ${replay ? (symptom ? "replay-symptom" : "replay-other") : ""}`}>
                  <strong>Replay {index + 1}</strong>
                  <span>{replay ? (symptom ? "Bug appeared" : replay.text) : done ? "Not run" : "Waiting"}</span>
                  {replay?.data?.destroyed === true && <span className="fine">Sandbox destroyed</span>}
                </div>
              );
            })}
          </div>
          <p className="fine">Script SHA-256: {String(frozen.data?.script_sha256)}</p>
        </section>
      )}
    </main>
  );
}
