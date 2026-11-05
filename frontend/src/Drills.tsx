import { useEffect, useRef, useState } from "react";
import { formatSeconds, type TaskEvent } from "./stages";

const DRILLS = [
  { id: "secrets", name: "Steal secrets", attack: "Searches the environment, processes and files for credentials." },
  { id: "network", name: "Phone home", attack: "Tries an outside address, the cloud metadata service and Docker." },
  { id: "delete", name: "Delete everything", attack: "Runs rm -rf / and writes to /etc and /usr." },
  { id: "disk", name: "Fill the disk", attack: "Writes until something stops it." },
  { id: "fork", name: "Fork bomb", attack: "Starts processes until something stops it." },
  { id: "memory", name: "Eat all memory", attack: "Allocates memory until something stops it." },
  { id: "loop", name: "Never stop", attack: "Spins forever, and ignores everything." },
];

type Check = { name: string; passed: boolean; detail: string };
type DrillRun = { taskId: string; state: "running" | "done"; outcome?: string; rule?: string; checks?: Check[]; seconds?: number };
type Host = { canary_sha256: string | null; disk_free_bytes: number; load_1m: number; running_sandboxes: number };

export function Drills() {
  const [runs, setRuns] = useState<Record<string, DrillRun>>({});
  const [host, setHost] = useState<Host | null>(null);
  const [firstCanary, setFirstCanary] = useState<string | null>(null);
  const [apiUp, setApiUp] = useState(true);
  const [beats, setBeats] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [runningAll, setRunningAll] = useState(false);
  const stopAll = useRef(false);

  useEffect(() => {
    const sample = () =>
      fetch("/api/health/host")
        .then(async (response) => {
          setApiUp(response.ok);
          if (response.ok) {
            const body: Host = await response.json();
            setHost(body);
            setBeats((count) => count + 1);
            setFirstCanary((current) => current ?? body.canary_sha256);
          }
        })
        .catch(() => setApiUp(false));
    sample();
    const timer = window.setInterval(sample, 2000);
    return () => {
      window.clearInterval(timer);
      stopAll.current = true;
    };
  }, []);

  function start(drill: string): Promise<void> {
    setError(null);
    return fetch("/api/drills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ drill }),
    }).then(async (response) => {
      if (response.status !== 202) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? "The attack didn’t start. Try again in a minute.");
      }
      const taskId: string = (await response.json()).id;
      setRuns((current) => ({ ...current, [drill]: { taskId, state: "running" } }));
      await new Promise<void>((resolve) => {
        const stream = new EventSource(`/api/tasks/${taskId}/events`);
        stream.onmessage = (message) => {
          const event: TaskEvent = JSON.parse(message.data);
          if (event.type === "limit_hit") {
            setRuns((current) => ({ ...current, [drill]: { ...current[drill], checks: event.data?.checks as Check[], seconds: event.data?.seconds as number } }));
          }
          if (event.type === "outcome") {
            setRuns((current) => ({ ...current, [drill]: { ...current[drill], outcome: event.text, rule: String(event.data?.rule ?? "") } }));
          }
          if (event.type === "status_changed" && event.text === "done") {
            stream.close();
            setRuns((current) => ({ ...current, [drill]: { ...current[drill], state: "done" } }));
            resolve();
          }
        };
      });
    });
  }

  function runOne(drill: string) {
    start(drill).catch((failure: Error) => setError(failure.message || "The server didn’t answer. Try again."));
  }

  async function runAll() {
    setRunningAll(true);
    stopAll.current = false;
    try {
      for (const drill of DRILLS) {
        if (stopAll.current) break;
        await start(drill.id);
      }
    } catch (failure) {
      setError((failure as Error).message || "The server didn’t answer. Try again.");
    }
    setRunningAll(false);
  }

  const results = Object.values(runs).filter((run) => run.state === "done");
  const contained = results.filter((run) => run.outcome === "CONTAINED").length;
  const busy = runningAll || Object.values(runs).some((run) => run.state === "running");
  const canaryChanged = Boolean(firstCanary && host?.canary_sha256 && firstCanary !== host.canary_sha256);

  return (
    <main className="drill-page">
      <section className="drill-head">
        <div>
          <h1>Try to break out of the sandbox</h1>
          <p>Each attack is real code, run in a fresh sandbox as if the AI had written it. The results are measured from outside, on the host, so the attack can’t lie about them.</p>
        </div>
        <div className="drill-actions">
          <button className="primary" disabled={busy} onClick={runAll}>{runningAll ? "Running the attacks" : "Run all 7 attacks"}</button>
          {results.length > 0 && !busy && (
            <p className="drill-summary" role="status">{contained} of {results.length} contained</p>
          )}
        </div>
      </section>

      <section className="host" aria-label="Host health, measured outside any sandbox">
        <h2>The host right now</h2>
        <dl>
          <div className={apiUp ? "" : "host-bad"}><dt>API</dt><dd>{apiUp ? `Responding, ${beats} checks` : "Not responding"}</dd></div>
          <div><dt>Sandboxes running</dt><dd>{host ? host.running_sandboxes : "Checking"}</dd></div>
          <div><dt>Host load</dt><dd>{host ? host.load_1m.toFixed(2) : "Checking"}</dd></div>
          <div><dt>Host disk free</dt><dd>{host ? `${(host.disk_free_bytes / 1024 ** 3).toFixed(1)} GiB` : "Checking"}</dd></div>
          <div className={canaryChanged ? "host-bad" : ""}><dt>Host canary file</dt><dd>{!host?.canary_sha256 ? "Checking" : canaryChanged ? "Changed" : "Unchanged"}</dd></div>
        </dl>
      </section>

      {error && <p role="alert" className="error">{error}</p>}

      <div className="drills">
        {DRILLS.map((drill) => {
          const run = runs[drill.id];
          const failedChecks = run?.checks?.filter((check) => !check.passed).length ?? 0;
          const outcome = run?.state === "done" ? run.outcome : undefined;
          return (
            <section key={drill.id} className={`drill ${outcome === "CONTAINED" ? "drill-contained" : outcome ? "drill-breached" : run ? "drill-running" : ""}`}>
              <div className="drill-title">
                <h2>{drill.name}</h2>
                {outcome && <span className="mini-stamp">{outcome === "CONTAINED" ? "Contained" : outcome === "NOT_CONTAINED" ? "Not contained" : "Failed"}</span>}
              </div>
              <p>{drill.attack}</p>
              {run?.state === "running" && <p className="drill-now" role="status"><span className="pulse" aria-hidden="true" />Running in a fresh sandbox</p>}
              {run?.checks && (
                <details open={failedChecks > 0}>
                  <summary>
                    {run.checks.length - failedChecks} of {run.checks.length} checks passed{run.seconds ? `, in ${formatSeconds(run.seconds)}` : ""}
                  </summary>
                  <ul>
                    {run.checks.map((check) => (
                      <li key={check.name} className={check.passed ? "pass" : "fail"}>
                        <strong>{check.passed ? "Passed" : "Failed"}:</strong> {check.name}. <span className="fine">{check.detail}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="drill-foot">
                <button disabled={busy} onClick={() => runOne(drill.id)}>{run ? "Run again" : "Run this attack"}</button>
                {outcome && <a href={`/api/tasks/${run.taskId}/manifest`} target="_blank" rel="noreferrer noopener">See the evidence</a>}
              </div>
            </section>
          );
        })}
      </div>
    </main>
  );
}
