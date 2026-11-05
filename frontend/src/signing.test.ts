import { getPublicKeyAsync, signAsync, utils } from "@noble/ed25519";
import { describe, expect, it } from "vitest";
import { canonical, checkManifest } from "./signing";

function base64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes));
}

async function signed(body: Record<string, unknown>) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(body)));
  const manifest_sha256 = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  const secret = utils.randomSecretKey();
  const message = new TextEncoder().encode(canonical({ ...body, manifest_sha256 }));
  return {
    ...body,
    manifest_sha256,
    signature: {
      algorithm: "Ed25519",
      public_key: base64(await getPublicKeyAsync(secret)),
      value: base64(await signAsync(message, secret)),
    },
  };
}

describe("canonical", () => {
  it("matches the backend's form", () => {
    expect(canonical({ b: [1, { z: null, a: true }], a: "é\n" })).toBe('{"a":"é\\n","b":[1,{"a":true,"z":null}]}');
  });
});

describe("checkManifest", () => {
  it("accepts an intact manifest", async () => {
    const result = await checkManifest(await signed({ outcome: "APPROVED", n: 1 }), null);
    expect(result).toMatchObject({ signed: true, hashMatches: true, signatureMatches: true, keyMatches: null });
  });

  it("catches one changed field", async () => {
    const manifest = await signed({ outcome: "REJECTED", n: 1 });
    const result = await checkManifest({ ...manifest, outcome: "APPROVED" }, null);
    expect(result.hashMatches).toBe(false);
    expect(result.signatureMatches).toBe(false);
  });

  it("catches a forged hash, because the signature covers it", async () => {
    const manifest = await signed({ n: 1 });
    expect((await checkManifest({ ...manifest, manifest_sha256: "0".repeat(64) }, null)).signatureMatches).toBe(false);
  });

  it("catches a different signer when the expected key is known", async () => {
    const manifest = await signed({ n: 1 });
    expect((await checkManifest(manifest, "someone-else")).keyMatches).toBe(false);
    expect((await checkManifest(manifest, manifest.signature.public_key)).keyMatches).toBe(true);
  });

  it("reports an unsigned manifest as unsigned", async () => {
    expect((await checkManifest({ n: 1 }, null)).signed).toBe(false);
  });
});
