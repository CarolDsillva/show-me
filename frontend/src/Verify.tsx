import { useEffect, useState } from "react";
import { checkManifest, type CheckResult } from "./signing";

type Manifest = Record<string, unknown>;

function Row({ label, ok, detail }: { label: string; ok: boolean; detail: string }) {
  return (
    <li className={ok ? "pass" : "fail"}>
      <strong>{ok ? "Passed" : "Failed"}:</strong> {label}. <span className="fine">{detail}</span>
    </li>
  );
}

export function Verify({ taskId, onBack }: { taskId: string; onBack: () => void }) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [serverKey, setServerKey] = useState<string | null>(null);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [tampered, setTampered] = useState<CheckResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetch(`/api/tasks/${taskId}/manifest`), fetch("/api/signing-key")])
      .then(async ([manifestResponse, keyResponse]) => {
        if (!manifestResponse.ok) throw new Error("This run has no evidence yet. It’s written when the run reaches a result.");
        const body: Manifest = await manifestResponse.json();
        const key = keyResponse.ok ? (await keyResponse.json()).public_key : null;
        setManifest(body);
        setServerKey(key);
        setResult(await checkManifest(body, key));
      })
      .catch((failure: Error) => setError(failure.message || "The server didn’t answer. Try again."));
  }, [taskId]);

  async function tamper() {
    if (!manifest) return;
    const outcome = manifest.outcome as { value?: string };
    const changed = { ...manifest, outcome: { ...outcome, value: outcome?.value === "APPROVED" ? "REJECTED" : "APPROVED" } };
    setTampered(await checkManifest(changed, serverKey));
  }

  return (
    <main className="verify">
      <h1>Check this run’s evidence</h1>
      <p className="lede">Your browser does this check itself, using the record and the server’s public key.</p>
      {error && <p role="alert" className="error">{error}</p>}
      {result && (
        <section className="verify-card">
          {!result.signed ? (
            <>
              <h2>This run isn’t signed</h2>
              <p>It finished before signing was added, so only its hash can be checked.</p>
              <ul><Row label="The hash matches the record" ok={result.hashMatches} detail="SHA-256 of the record" /></ul>
            </>
          ) : (
            <>
              <h2>{result.hashMatches && result.signatureMatches && result.keyMatches !== false ? "The record is intact" : "The record doesn’t match"}</h2>
              <ul>
                <Row label="The hash matches the record" ok={result.hashMatches} detail="SHA-256 of the record" />
                <Row label="The signature matches" ok={result.signatureMatches} detail="Ed25519" />
                {result.keyMatches !== null && <Row label="Signed by this server’s key" ok={result.keyMatches} detail={result.publicKey ?? ""} />}
              </ul>
              <div className="actions">
                <button onClick={tamper}>Change the outcome and check again</button>
              </div>
              {tampered && (
                <p className={`tamper ${tampered.signatureMatches ? "tamper-bad" : ""}`} role="status">
                  {tampered.signatureMatches ? "The changed record still passed, which means this check is broken." : "Changing one field broke both the hash and the signature, so the change is caught."}
                </p>
              )}
            </>
          )}
          <p className="fine limits">This shows the record hasn’t changed since this server signed it. It doesn’t show the server was honest when it signed.</p>
          <div className="links">
            <a href={`/api/tasks/${taskId}/manifest`} target="_blank" rel="noreferrer noopener">See the full record</a>
            <button onClick={onBack}>Back to the run</button>
          </div>
        </section>
      )}
    </main>
  );
}
