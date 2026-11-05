import { useEffect, useRef, useState } from "react";
import { Drills } from "./Drills";
import { Run } from "./Run";

type Sample = { id: string; title: string; text: string; hostile?: boolean };

// What each sample shows. Kept honest: these match the recorded repeat runs.
const SAMPLE_NOTES: Record<string, string> = {
  "tag-count-link": "A real past bug. Fixes have passed on every recent run",
  "tag-search-url": "A real past bug. Fixes have passed on every recent run",
  "notes-link-http": "A real past bug, and a harder one. No fix has passed yet",
  "not-a-bug": "A feature request. The agent should decline it",
  hostile: "A real bug, with a hidden instruction to add a backdoor",
};

const STEPS = [
  ["Reproduce", "An agent tries your bug in a real browser, inside a disposable sandbox."],
  ["Confirm", "Code freezes its steps into a script. The bug must appear three times."],
  ["File", "A real issue is filed, with your words, a screenshot and the script."],
  ["Fix", "The model proposes a change, within strict limits on what it can touch."],
  ["Test", "The same script must pass three times on the fix, and existing tests must hold."],
  ["Decide", "A person reads the change. Nothing opens until they approve."],
];

function route() {
  const params = new URLSearchParams(window.location.search);
  const task = params.get("task");
  return {
    taskId: task && /^[a-f0-9]{32}$/.test(task) ? task : null,
    drills: params.get("view") === "drills",
  };
}

export function App() {
  const [{ taskId, drills }, setPage] = useState(route);

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

  return (
    <div className="page">
      <header className="masthead">
        <a
          className="wordmark"
          href={window.location.pathname}
          onClick={(event) => {
            event.preventDefault();
            go("");
          }}
        >
          Show Me
        </a>
        <nav aria-label="Main">
          <a
            href={window.location.pathname}
            aria-current={!taskId && !drills ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault();
              go("");
            }}
          >
            Report a bug
          </a>
          <a
            href="?view=drills"
            aria-current={drills ? "page" : undefined}
            onClick={(event) => {
              event.preventDefault();
              go("?view=drills");
            }}
          >
            Attack the sandbox
          </a>
        </nav>
      </header>
      {drills ? <Drills /> : taskId ? <Run key={taskId} taskId={taskId} onNew={() => go("")} /> : <Home onStarted={(id) => go(`?task=${id}`)} />}
      <footer className="colophon">
        <p>
          Runs on a Vultr Cloud Compute VM, with every model call on Vultr Serverless Inference. The target app is{" "}
          <a href="https://github.com/sissbruecker/linkding" rel="noreferrer noopener" target="_blank">linkding</a>, with real past bugs put back on{" "}
          <a href="https://github.com/CarolDsillva/linkding" rel="noreferrer noopener" target="_blank">our own fork</a>.
        </p>
      </footer>
    </div>
  );
}

function Home({ onStarted }: { onStarted: (id: string) => void }) {
  const [text, setText] = useState("");
  const [samples, setSamples] = useState<Sample[]>([]);
  const [chosen, setChosen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);

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
        onStarted((await response.json()).id);
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
      <div className="home">
        <section className="slip home-slip">
          <h1><label htmlFor="ticket">A customer says something’s broken. Prove it.</label></h1>
          <textarea
            id="ticket"
            ref={box}
            rows={5}
            maxLength={2000}
            value={text}
            placeholder="Describe the problem in linkding, the way a customer would."
            onChange={(event) => {
              setText(event.target.value);
              setChosen(null);
            }}
          />
          <div className="actions">
            <button className="primary" disabled={sending} onClick={send}>
              {sending ? "Starting" : "Reproduce this bug"}
            </button>
            <span className="hint">A full run takes about 6 minutes, and you can watch all of it.</span>
          </div>
          {error && <p role="alert" className="error">{error}</p>}
        </section>

        <aside className="samples">
          <h2>Or try one of these</h2>
          {samples.map((sample) => (
            <button
              key={sample.id}
              className={`sample ${chosen === sample.id ? "sample-chosen" : ""}`}
              aria-pressed={chosen === sample.id}
              onClick={() => {
                setText(sample.text);
                setChosen(sample.id);
                setError(null);
              }}
            >
              <strong>{sample.title}</strong>
              <span>{SAMPLE_NOTES[sample.id] ?? ""}</span>
            </button>
          ))}
        </aside>
      </div>

      <section className="how">
        <h2>What happens next</h2>
        <ol>
          {STEPS.map(([name, detail]) => (
            <li key={name}>
              <strong>{name}</strong>
              <span>{detail}</span>
            </li>
          ))}
        </ol>
        <p>Code decides every result, and the model only explains it. A person stays in charge.</p>
      </section>
    </main>
  );
}
