import { verifyAsync } from "@noble/ed25519";

// Must match canonical() in backend/app/evidence/store.py: sorted keys, no spaces, raw Unicode.
export function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function fromBase64(text: string): Uint8Array {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export type CheckResult = {
  signed: boolean;
  hashMatches: boolean;
  signatureMatches: boolean;
  keyMatches: boolean | null;
  publicKey: string | null;
};

/** Runs entirely in the browser. It uses no result from the server beyond the manifest and key. */
export async function checkManifest(manifest: Record<string, unknown>, expectedKey: string | null): Promise<CheckResult> {
  const { signature, manifest_sha256: claimed, ...body } = manifest;
  const hashMatches = (await sha256Hex(canonical(body))) === claimed;
  const details = signature as { public_key?: string; value?: string } | undefined;
  if (!details?.public_key || !details.value) {
    return { signed: false, hashMatches, signatureMatches: false, keyMatches: null, publicKey: null };
  }
  let signatureMatches = false;
  try {
    signatureMatches = await verifyAsync(
      fromBase64(details.value),
      new TextEncoder().encode(canonical({ ...body, manifest_sha256: claimed })),
      fromBase64(details.public_key),
    );
  } catch {
    signatureMatches = false;
  }
  return {
    signed: true,
    hashMatches,
    signatureMatches,
    keyMatches: expectedKey ? expectedKey === details.public_key : null,
    publicKey: details.public_key,
  };
}
