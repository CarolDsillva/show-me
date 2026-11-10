import { useEffect, useState } from "react";

export type BenchmarkRun = { task: string; outcome: string; seconds: number; tokens: number | null; cost_usd: number | null; issue: string | null };
export type BenchmarkBug = { title: string; upstream: string | null; runs: BenchmarkRun[] };
export type Benchmark = { target_commit: string; updated_at?: string; bugs: Record<string, BenchmarkBug> };

const REPRODUCED = new Set(["REPRODUCED", "FIX_VERIFIED", "NO_VERIFIED_FIX", "APPROVED", "REJECTED"]);
const VERIFIED = new Set(["FIX_VERIFIED", "APPROVED", "REJECTED"]);

export function useBenchmark(): Benchmark | null {
  const [data, setData] = useState<Benchmark | null>(null);
  useEffect(() => {
    fetch("/results.json")
      .then((response) => (response.ok && response.headers.get("content-type")?.includes("json") ? response.json() : null))
      .then(setData)
      .catch(() => setData(null));
  }, []);
  return data;
}

export function summarize(data: Benchmark) {
  const runs = Object.values(data.bugs).flatMap((bug) => bug.runs);
  const costs = runs.map((run) => run.cost_usd).filter((cost): cost is number => typeof cost === "number" && cost > 0);
  return {
    bugs: Object.keys(data.bugs).length,
    runs: runs.length,
    reproduced: runs.filter((run) => REPRODUCED.has(run.outcome)).length,
    verified: runs.filter((run) => VERIFIED.has(run.outcome)).length,
    averageCost: costs.length ? costs.reduce((sum, cost) => sum + cost, 0) / costs.length : null,
    averageMinutes: runs.length ? runs.reduce((sum, run) => sum + run.seconds, 0) / runs.length / 60 : null,
  };
}

export function money(value: number | null): string {
  if (value === null) return "Unknown";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(3)}`;
}

function outcomeLabel(outcome: string): string {
  if (VERIFIED.has(outcome)) return "Fix verified";
  if (outcome === "NO_VERIFIED_FIX") return "Bug filed, no fix";
  if (outcome === "NOT_REPRODUCED") return "Not reproduced";
  if (outcome === "FAILED") return "Run failed";
  return outcome;
}

export function Results() {
  const data = useBenchmark();
  const totals = data ? summarize(data) : null;
  return (
    <main className="section-page">
      <header className="page-head">
        <h1>Results</h1>
        <p>Real past bugs in linkding, recreated by reverting their real fixes. Each row is the same customer ticket, run again from scratch on the live system. Every run links to its evidence.</p>
      </header>
      {!data || !totals ? (
        <p className="card empty">The first results will appear here when the current benchmark finishes.</p>
      ) : (
        <>
          <section className="stats" aria-label="Totals">
            <div><strong>{totals.bugs}</strong><span>real past bugs</span></div>
            <div><strong>{totals.reproduced} of {totals.runs}</strong><span>runs reproduced the bug</span></div>
            <div><strong>{totals.verified} of {totals.runs}</strong><span>runs ended with a verified fix</span></div>
            <div><strong>{money(totals.averageCost)}</strong><span>average model cost per ticket</span></div>
          </section>
          <section className="card table-card">
            <table className="results">
              <thead>
                <tr><th>Bug</th><th>Reproduced</th><th>Fix verified</th><th>Average cost</th><th>Runs</th></tr>
              </thead>
              <tbody>
                {Object.entries(data.bugs).map(([id, bug]) => {
                  const costs = bug.runs.map((run) => run.cost_usd).filter((cost): cost is number => typeof cost === "number" && cost > 0);
                  return (
                    <tr key={id}>
                      <td>
                        <strong>{bug.title}</strong>
                        {bug.upstream && <a className="sub" href={bug.upstream} target="_blank" rel="noreferrer noopener">The real upstream fix</a>}
                      </td>
                      <td>{bug.runs.filter((run) => REPRODUCED.has(run.outcome)).length} of {bug.runs.length}</td>
                      <td>{bug.runs.filter((run) => VERIFIED.has(run.outcome)).length} of {bug.runs.length}</td>
                      <td>{money(costs.length ? costs.reduce((sum, cost) => sum + cost, 0) / costs.length : null)}</td>
                      <td>
                        <ul className="run-links">
                          {bug.runs.map((run, index) => (
                            <li key={run.task}><a href={`?task=${run.task}`}>Run {index + 1}: {outcomeLabel(run.outcome)}</a></li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>
          <section className="notes">
            <h2>How to read this</h2>
            <ul>
              <li>This is a small sample, so it isn’t a success rate you should expect on other software.</li>
              <li>A verified fix passed the frozen script and existing tests. It may still be the wrong fix, which is why a person reads it before anything opens.</li>
              <li>Cost is model usage at Vultr’s listed per-token rates. It leaves out any subscription fee and the server.</li>
              <li>Baseline commit: <code>{data.target_commit}</code></li>
            </ul>
          </section>
        </>
      )}
    </main>
  );
}
