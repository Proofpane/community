import { readFileSync } from "node:fs";
import * as N from "node:crypto";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterAll, describe, expect, it, vi } from "vitest";

// The page's own approval of another device's sign-in, against the server itself: tests/test_device_approval.py
// serves the app on a local port and runs this file with PP_DEVICE_APPROVAL naming what it made there (the owner's
// sign-in and password, a passkey of the owner's as an ES256 key and its credential id, and two devices waiting to be
// signed in). The person approves the first by its typed code and the password, the second from a link and a
// passkey; the Python test then has each device collect its sign-in. Without PP_DEVICE_APPROVAL it is skipped: the
// same flow against a stand-in server is in components/AccountDialog.test.tsx.
type Given = {
  server: string; token: string; user: { id: string; email: string }; password: string; origin: string; rp_id: string;
  key: string; credential_id: string;
  first: { code: string; name: string; id: string; poll: string }; second: { code: string; name: string };
};
const given: Given | null = process.env.PP_DEVICE_APPROVAL ? JSON.parse(readFileSync(process.env.PP_DEVICE_APPROVAL, "utf8")) : null;

const b64u = (bytes: Uint8Array) => Buffer.from(bytes).toString("base64url");
const sha256 = (bytes: Uint8Array) => new Uint8Array(N.createHash("sha256").update(bytes).digest());
const bytesOf = (v: BufferSource) => (v instanceof ArrayBuffer ? new Uint8Array(v) : new Uint8Array(v.buffer, v.byteOffset, v.byteLength)).slice();
const concat = (...parts: ArrayLike<number>[]) => { const out: number[] = []; for (const p of parts) out.push(...Array.from(p)); return new Uint8Array(out); };

/** An ECDSA signature as WebAuthn sends it, DER, from WebCrypto's r || s. */
function der(raw: Uint8Array): Uint8Array {
  const int = (b: Uint8Array) => {
    let i = 0;
    while (i < b.length - 1 && b[i] === 0) i++;
    const v = b[i] & 0x80 ? concat([0], b.subarray(i)) : b.subarray(i);
    return concat([0x02, v.length], v);
  };
  const body = concat(int(raw.subarray(0, 32)), int(raw.subarray(32)));
  return concat([0x30, body.length], body);
}

afterAll(() => { cleanup(); vi.unstubAllGlobals(); vi.resetModules(); localStorage.clear(); });

describe.skipIf(!given)("approving a device's sign-in, against the server", () => {
  it("asks for the person's word before approving, made for the device it approves, by the password and by a passkey", async () => {
    const g = given as Given;
    const key = await crypto.subtle.importKey("pkcs8", Buffer.from(g.key, "base64url"), { name: "ECDSA", namedCurve: "P-256" }, false, ["sign"]);
    // the owner's passkey, as a platform authenticator answers: over the challenge the page hands it, for this site
    const asked: Uint8Array[] = [];
    const get = vi.fn(async (opts: { publicKey: { challenge: BufferSource } }) => {
      const challenge = bytesOf(opts.publicKey.challenge);
      asked.push(challenge);
      const client = new TextEncoder().encode(JSON.stringify({ type: "webauthn.get", challenge: b64u(challenge), origin: g.origin, crossOrigin: false }));
      const data = concat(sha256(new TextEncoder().encode(g.rp_id)), [0x05], [0, 0, 0, 0]);
      const signed = new Uint8Array(await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, key, concat(data, sha256(client))));
      return { id: g.credential_id, type: "public-key", getClientExtensionResults: () => ({}),
        response: { clientDataJSON: client.buffer, authenticatorData: data.buffer, signature: der(signed).buffer, userHandle: null } };
    });
    vi.resetModules();
    vi.stubGlobal("PublicKeyCredential", function PublicKeyCredential() {});
    Object.defineProperty(navigator, "credentials", { value: { get }, configurable: true });
    localStorage.setItem("pp-open-server", g.server);         // the page talks to the server named, with this sign-in
    localStorage.setItem("pp-open-token", g.token);
    const { useStore } = await import("./store");
    useStore.setState({ user: g.user });
    const { AccountDialog } = await import("./components/AccountDialog");

    // the first device: its code typed as the person reads it, then the password
    render(<AccountDialog tab="device" onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("The code the device shows"), { target: { value: g.first.code.toLowerCase().replace("-", " ") } });
    fireEvent.click(screen.getByRole("button", { name: "Look" }));
    expect(await screen.findByText(g.first.name)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    const first = await screen.findByRole("group", { name: `Signing in “${g.first.name}” (code ${g.first.code})` });
    expect(first.textContent).toContain("takes one of your passkeys or your password as well as this sign-in");
    expect(first.textContent).toContain("Once approved, it is signed in with this account for 7 days, or until you sign out everywhere.");
    // nothing is approved until the person gives their word
    const waiting = await fetch(`${g.server}/api/auth/device/poll`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: g.first.id, poll: g.first.poll }) });
    expect(await waiting.json()).toEqual({ status: "waiting" });
    fireEvent.change(within(first).getByLabelText("Your password"), { target: { value: "not the password" } });
    fireEvent.click(within(first).getByRole("button", { name: "Confirm" }));
    expect(await within(first).findByText(/That did not confirm it/)).toBeTruthy();
    fireEvent.change(within(first).getByLabelText("Your password"), { target: { value: g.password } });
    fireEvent.click(within(first).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByText(`${g.first.name} is signed in with this account.`)).toBeTruthy();
    expect(get).not.toHaveBeenCalled();
    cleanup();

    // the second: its code in a link, compared with the device's own, then a passkey
    render(<AccountDialog tab="device" deviceCode={g.second.code} onClose={() => {}} />);
    expect((await screen.findByLabelText("The code from the link")).textContent).toBe(g.second.code);
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const second = await screen.findByRole("group", { name: `Signing in “${g.second.name}” (code ${g.second.code})` });
    fireEvent.click(within(second).getByRole("button", { name: "Use a passkey" }));
    expect(await screen.findByText(`${g.second.name} is signed in with this account.`)).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(1);
    expect(asked[0]).toHaveLength(32);
  });
});
