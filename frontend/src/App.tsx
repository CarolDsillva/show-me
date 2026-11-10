import { useEffect, useRef, useState } from "react";
import { Drills } from "./Drills";
import { money, Results, summarize, useBenchmark } from "./Results";
import { Run } from "./Run";
import { Verify } from "./Verify";

type Sample = { id: string; title: string; text: string; hostile?: boolean };

// Links to real recorded runs. The pages show exactly what those runs recorded.
const EXAMPLES = [
  { id: "f48a18e0ce46476b9582a3d9d7b188a9", title: "Fix approved", note: "From a customer’s complaint to an open pull request.", tone: "good" },
  { id: "f2f036fee65541daae11c39026d67d55", title: "Hidden instructions", note: "A ticket tells the agent to add a backdoor. It gets flagged.", tone: "warn" },
  { id: "75f11bef07aa4e65972e0d5bb93f3725", title: "No fix passed", note: "The bug is filed, and the system says no fix worked.", tone: "bad" },
  { id: "1398f3031b564ad3890357c818b0d949", title: "Not a bug", note: "A feature request, declined with nothing filed.", tone: "plain" },
];

const STEPS = [
  ["Reproduce", "An agent tries the bug in a real browser, inside a disposable sandbox."],
  ["Confirm", "Code freezes its steps into a script. The bug must appear three times."],
  ["File", "A real issue is filed with the customer’s words, a screenshot and the script."],
  ["Fix", "The model proposes a change, within strict limits on what it can touch."],
  ["Test", "The same script must pass three times, and existing tests must hold."],
  ["Decide", "A developer reads the change. Nothing opens until they approve."],
];

function route() {
  const params = new URLSearchParams(window.location.search);
  const valid = (value: string | null) => (value && /^[a-f0-9]{32}$/.test(value) ? value : null);
  const view = params.get("view");
  return {
    taskId: valid(params.get("task")),
    verifyId: valid(params.get("verify")),
    drills: view === "drills",
    results: view === "results",
  };
}

function Logo() {
  return (
    <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="#4338ca" />
      <path d="M9 17l5 5 10-12" fill="none" stroke="#ffffff" strokeWidth="3.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function App() {
  const [page, setPage] = useState(route);
  const { taskId, verifyId, drills, results } = page;

  useEffect(() => {
    const onPop = () => setPage(route());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function go(search: string) {
    window.history.pushState(null, "", search || window.location.pathname);
    setPage(route());
    window.scrollTo(0, 0);
  }

  function link(search: string) {
    return {
      href: search || window.location.pathname,
      onClick: (event: React.MouseEvent) => {
        event.preventDefault();
        go(search);
      },
    };
  }

  const onHome = !taskId && !verifyId && !drills && !results;

  return (
    <div className="shell">
      <header className="topbar">
        <div className="topbar-inner">
          <a className="brand" {...link("")}><Logo /> Show Me</a>
          <nav aria-label="Main">
            <a {...link("")} aria-current={onHome ? "page" : undefined}>Product</a>
            <a {...link("?view=results")} aria-current={results ? "page" : undefined}>Results</a>
            <a {...link("?view=drills")} aria-current={drills ? "page" : undefined}>Security</a>
          </nav>
          <a className="button primary small" {...link("")} onClick={(event) => {
            event.preventDefault();
            go("");
            window.setTimeout(() => document.getElementById("ticket")?.focus(), 50);
          }}>Report a bug</a>
        </div>
      </header>
      <div className="content">
        {verifyId ? <Verify taskId={verifyId} onBack={() => go(`?task=${verifyId}`)} />
          : drills ? <Drills />
          : results ? <Results />
          : taskId ? <Run key={taskId} taskId={taskId} onNew={() => go("")} />
          : <Home onOpen={(id) => go(`?task=${id}`)} onResults={() => go("?view=results")} onSecurity={() => go("?view=drills")} />}
      </div>
      <footer className="footer">
        <div className="footer-inner">
          <span className="brand small-brand"><Logo /> Show Me</span>
          <p>
            Runs on a Vultr Cloud Compute VM, with every model call on Vultr Serverless Inference. The target app is{" "}
            <a href="https://github.com/sissbruecker/linkding" rel="noreferrer noopener" target="_blank">linkding</a>, with real past bugs put back on{" "}
            <a href="https://github.com/CarolDsillva/linkding" rel="noreferrer noopener" target="_blank">our own fork</a>.
          </p>
        </div>
      </footer>
    </div>
  );
}

function Home({ onOpen, onResults, onSecurity }: { onOpen: (id: string) => void; onResults: () => void; onSecurity: () => void }) {
  const [text, setText] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const benchmark = useBenchmark();
  const totals = benchmark ? summarize(benchmark) : null;

  useEffect(() => {
    fetch("/api/samples")
      .then((response) => (response.ok ? response.json() : []))
      .then(setSamples)
      .catch(() => setSamples([]));
  }, []);

  async function send() {
    if (text.trim().length < 10) {
      setError("Describe the problem in at least 10 characters.");
      box.current?.focus();
      return;
    }
    setSending(true);
    setError(null);
    try {
      const response = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (response.status === 202) {
        onOpen((await response.json()).id);
        return;
      }
      if (response.status === 429) {
        setError((await response.json()).detail);
      } else {
        setError("Write the problem in 10 to 2,000 characters, then try again.");
      }
    } catch {
      setError("The server didn’t answer. Check your connection and try again.");
    }
    setSending(false);
  }

  return (
    <main>
      <section className="hero">
        <div className="hero-copy">
          <h1>Turn “it’s broken” into a proven fix</h1>
          <p className="hero-sub">
            Show Me reproduces a customer’s bug in a real browser, files it with evidence, and tests a fix with the same script. Your developers review the proof and decide.
          </p>
          <ul className="hero-points">
            <li>Every run happens in a disposable sandbox</li>
            <li>Code decides each result, not the model</li>
            <li>Nothing ships until a person approves</li>
          </ul>
          <div className="hero-links">
            <button className="button secondary" onClick={() => onOpen(EXAMPLES[0].id)}>See a finished run</button>
            <button className="button ghost" onClick={onSecurity}>Attack the sandbox</button>
          </div>
        </div>

        <section className="card composer">
          <h2><label htmlFor="ticket">Try it on a real app</label></h2>
          <p className="composer-sub">Describe a problem in linkding, the way a customer would. A full run takes about 6 minutes, and you can watch all of it.</p>
          <textarea
            id="ticket"
            ref={box}
            rows={4}
            maxLength={2000}
            value={text}
            placeholder="When I click the number next to a tag, I never see my bookmarks…"
            onChange={(event) => {
              setText(event.target.value);
              setChosen(null);
            }}
          />
          {error && <p role="alert" className="error">{error}</p>}
          <button className="button primary wide" disabled={sending} onClick={send}>
            {sending ? "Starting" : "Reproduce this bug"}
          </button>
          <div className="sample-head">Or start from a sample</div>
          <div className="chips">
            {samples.map((sample) => (
              <button
                key={sample.id}
                className={`chip ${chosen === sample.id ? "chip-on" : ""}`}
                aria-pressed={chosen === sample.id}
                onClick={() => {
                  setText(sample.text);
                  setChosen(sample.id);
                  setError(null);
                }}
              >
                {sample.title}
              </button>
            ))}
          </div>
        </section>
      </section>

      {totals && totals.runs > 0 && (
        <section className="stats" aria-label="Benchmark results">
          <div><strong>{totals.bugs}</strong><span>real past bugs tested</span></div>
          <div><strong>{totals.reproduced} of {totals.runs}</strong><span>runs reproduced the bug</span></div>
          <div><strong>{totals.verified} of {totals.runs}</strong><span>runs ended with a verified fix</span></div>
          <div><strong>{money(totals.averageCost)}</strong><span>average model cost per ticket</span></div>
          <button className="stats-link" onClick={onResults}>See every run</button>
        </section>
      )}

      <section className="band">
        <h2>How it works</h2>
        <ol className="steps-grid">
          {STEPS.map(([name, detail], index) => (
            <li key={name} className="card step-card">
              <span className="step-number">{index + 1}</span>
              <strong>{name}</strong>
              <span>{detail}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="band">
        <h2>See finished runs</h2>
        <p className="band-sub">Real recorded runs, ready to read without waiting.</p>
        <ul className="example-grid">
          {EXAMPLES.map((example) => (
            <li key={example.id}>
              <a className={`card example tone-${example.tone}`} href={`?task=${example.id}`} onClick={(event) => { event.preventDefault(); onOpen(example.id); }}>
                <strong>{example.title}</strong>
                <span>{example.note}</span>
              </a>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
