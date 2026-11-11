import { act, cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "./e2eCrypto";
import * as K from "./e2eKeys";
import { CHANNEL, E2EProvider, IDLE_MS, IDLE_WORDS, SIGNED_OUT_WORDS, eventSlot, input, openSealed, stateOf, useE2E, useE2EStore, type E2E } from "./e2e";
import { FakeAuthenticator, FakeServer } from "./e2eTestkit";
import { useStore } from "./store";
import { CLASSICAL } from "./e2eTestkit";

const ACCOUNT = "acct-1";
let server: FakeServer;
let auth: FakeAuthenticator;
const channels: BroadcastChannel[] = [];

beforeEach(() => {
  server = new FakeServer();
  auth = new FakeAuthenticator();
  vi.stubGlobal("fetch", server.fetch);
  auth.install();
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  for (const c of channels.splice(0)) c.close();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  useE2EStore.setState(useE2EStore.getInitialState());
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
});

/** Encryption turned on with a passkey of the account's, its hold ended: the recovery code as it was shown. */
async function turnedOn(id = "credAAAA") {
  server.addPasskey(id, "Laptop");
  auth.add(id);
  const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
  const code = t.codeText();
  await t.prepare(await K.E2EApi.view());
  await t.firstTouch();
  await t.secondTouch();
  await t.send(true);
  server.settle();
  return code;
}

/** Every row the server keeps, as a copy: what an old copy of its database would hold. */
function snapshot() {
  const { statements, credentials, recovery, grants, chain, roster, contentKeys, passkeys, high } = server;
  return JSON.parse(JSON.stringify({ statements, credentials, recovery, grants, chain, roster, contentKeys, passkeys, high }));
}

/** What the hook says, as a component reads it. */
function Probe({ seen }: { seen: { current: E2E | null } }) {
  seen.current = useE2E();
  return null;
}

const signedIn = () => useStore.setState({ user: { id: ACCOUNT, email: "a@example.org", can_sign_in_again: true } });
const stateNow = () => stateOf(useE2EStore.getState());

describe("the tab's state and the opener", () => {
  it("is off with no keys, locked with keys, unlocked after a touch, and locked again by Lock", async () => {
    await useE2EStore.getState().load(ACCOUNT);
    expect(stateNow()).toBe("off");
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    expect(stateNow()).toBe("locked");
    expect(useE2EStore.getState().view?.on).toBe(true);
    await useE2EStore.getState().unlock();
    expect(stateNow()).toBe("unlocked");
    useE2EStore.getState().lock();
    expect(stateNow()).toBe("locked");
    expect(useE2EStore.getState().ring).toBeNull();
  });

  it("counts a generation that rises at every lock and every unlock, but not when it follows a change, so a lock and an unlock between two renders are seen", async () => {
    await turnedOn();
    signedIn();
    const seen = { current: null as E2E | null };
    render(<E2EProvider><Probe seen={seen} /></E2EProvider>);
    await vi.waitFor(() => expect(seen.current?.state).toBe("locked"));
    const g0 = seen.current!.generation;
    await act(() => seen.current!.unlock());
    const g1 = seen.current!.generation;
    expect(g1).toBeGreaterThan(g0);
    // locked and unlocked again before React renders: the state reads unlocked both times, the generation does not
    await act(async () => {
      useE2EStore.getState().lock();
      await useE2EStore.getState().unlock();
    });
    expect(seen.current!.state).toBe("unlocked");
    expect(seen.current!.generation).toBeGreaterThanOrEqual(g1 + 2);
    // following a rotation made elsewhere keeps it
    const g2 = seen.current!.generation;
    const elsewhere = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Rotate(elsewhere, await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect(useE2EStore.getState().ring?.keys.size).toBe(2);
    expect(seen.current!.generation).toBe(g2);
  });

  it("asks for the touch before anything is awaited, so a click reaches the browser's prompt (Safari asks for the click)", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    const get = vi.spyOn(navigator.credentials, "get");
    const unlocking = useE2EStore.getState().unlock();
    expect(get).toHaveBeenCalledTimes(1);
    await unlocking;
    expect(stateNow()).toBe("unlocked");
  });

  it("opens a sealed text with its slot once unlocked, refuses another slot, and gives nothing while locked", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    const k = server.contentKeys[0];
    const slot = eventSlot(ACCOUNT, "link-1", "step", null, null, "a".repeat(32));
    const env = await C.sealEnvelope(C.unb64u(k.public_key)!, C.utf8('{"said_at":null,"session":null,"text":"Built."}'), slot);
    expect(await openSealed(env, slot)).toEqual({ ok: false, why: K.LOCKED });
    await useE2EStore.getState().unlock();
    expect(await openSealed(env, slot)).toEqual({ ok: true, value: { said_at: null, session: null, text: "Built." } });
    expect(await openSealed(env, slot.replace("step", "done"))).toEqual({ ok: false, why: K.UNREADABLE });
    // locked while it was opening: what opened is not given out
    const opening = openSealed(env, slot);
    useE2EStore.getState().lock();
    expect(await opening).toEqual({ ok: false, why: K.LOCKED });
  });

  it("unlocks with the recovery code too", async () => {
    const code = await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlockWithCode(code);
    expect(stateNow()).toBe("unlocked");
    expect(useE2EStore.getState().ring?.via.kind).toBe("recovery");
  });

  it("reads the keys again once when an unlock fails on a change made elsewhere, and follows it", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    const elsewhere = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(elsewhere, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();                                  // the page's view is from before the revocation
    await useE2EStore.getState().unlock();
    expect(useE2EStore.getState().ring?.master.id).toBe(server.roster!.mk_id);
  });

  it("reads the keys once more before locking when what it read does not lead back, and locks when it still does not", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const elsewhere = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(elsewhere, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    const before = server.view();
    server.settle();
    const after = server.view();
    // read while the change was being made: the old master key named, and the new link of the chain already there
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    let reads = 0;
    server.answer = (call) => (call.path === "/api/ade/e2e" && reads++ === 0 ? [200, torn] : null);
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().ring?.master.id).toBe(server.roster!.mk_id);
    // and the view it keeps is the second read, the one it followed, not the torn one
    expect(useE2EStore.getState().view?.mk_id).toBe(server.roster!.mk_id);
    expect(useE2EStore.getState().view?.mk_id).not.toBe(torn.mk_id);
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [200, torn] : null);
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().ring).toBeNull();
    // it names the master key the tab held before it followed the revocation: an older one put back
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
  });

  it("keeps, read after read, a lock's reason the view alone does not give: keys that do not lead back to those it held", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    const before = server.view();
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);                        // followed: the tab holds the new master key
    const after = server.view();
    // the old master key named with the new link of the chain: as many statements as this browser saw, so not older by
    // the view (rolledBack), but not leading back to the master key the tab held
    const torn = { ...after, mk_id: before.mk_id, grants: before.grants, roster: before.roster };
    server.answer = (call) => (call.path === "/api/ade/e2e" ? [200, torn] : null);
    await useE2EStore.getState().load(ACCOUNT);
    const s = useE2EStore.getState();
    expect([s.ring, s.why, K.rolledBack(ACCOUNT, s.view!)]).toEqual([null, K.ROLLED_BACK, null]);
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
  });

  it("raises this browser's floor at each unlock and each change it follows, and refuses keys put back to before it", async () => {
    const code = await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const first = useE2EStore.getState().ring!.master.id;
    expect(K.floorOf(ACCOUNT)).toEqual([first]);
    const before = snapshot();
    const r = new K.Revoke(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);
    const second = useE2EStore.getState().ring!.master.id;
    expect(second).not.toBe(first);
    expect(K.floorOf(ACCOUNT)).toEqual([second]);
    // the server put back to before the revocation: the tab locks, and an unlock, by passkey or code, is refused
    Object.assign(server, before);
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().ring).toBeNull();
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
    await expect(useE2EStore.getState().unlock()).rejects.toThrow(K.ROLLED_BACK);
    await expect(useE2EStore.getState().unlockWithCode(code)).rejects.toThrow(K.ROLLED_BACK);
    expect(K.floorOf(ACCOUNT)).toEqual([second]);
    // keys unlocked another way are held as if unlocked here, the floor with them
    localStorage.clear();
    useE2EStore.getState().adopt(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT));
    expect(K.floorOf(ACCOUNT)).toEqual([first]);
  });

  it("follows a change only to keys that lead back to the newest this browser opened, in this tab or another", async () => {
    const code = await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();                        // this tab holds the first master key
    const first = snapshot();
    const m1 = useE2EStore.getState().ring!.master.id;
    // another tab follows a revocation, raising this browser's floor; this tab has not read the keys since
    const other = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(other, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    K.raiseFloor(ACCOUNT, await K.follow(other, await K.E2EApi.view()));
    // the server put back to the first master key, with a content key added under it by one who kept that key
    Object.assign(server, first);
    const { rk, RK } = await C.recoveryKeys(C.recoveryBytes(code)!, ACCOUNT);
    const g = server.grants.find((x) => x.to === "recovery")!;
    const m = await C.masterOf(await C.openGrant(rk, RK, g.sealed, ACCOUNT, "recovery", m1), ACCOUNT);
    const pair = await C.newPair(true);
    const keyId = await C.keyIdOf(pair.pub);
    server.contentKeys.push({ key_id: keyId, public_key: C.b64u(pair.pub), mk_id: m1, wrapped: await C.wrapContentKey(m, pair.priv, keyId, ACCOUNT) });
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().ring).toBeNull();
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
  });

  it("follows keys served classical where they were post-quantum, the same keys too, and locks: no change is made from them", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);                              // post-quantum, as the page makes keys now
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(false);
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    expect(useE2EStore.getState().ring?.pq).toBe(true);
    server.pq = false;                                            // the keys unchanged, said to be classical
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().ring).toBeNull();
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK_CLASSICAL);
  });

  it("unlocks keys this tab made once their hold ends, with no touch, as an unlock", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    await useE2EStore.getState().load(ACCOUNT);
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    const { ring, via } = await t.send(true);
    expect(ring).toBeNull();
    useE2EStore.getState().awaitKeys(via);
    await useE2EStore.getState().load(ACCOUNT);
    expect(stateNow()).toBe("off");                               // its hold has not ended: no keys yet
    const generation = useE2EStore.getState().generation;
    const touches = auth.ceremonies.length;
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);
    expect(stateNow()).toBe("unlocked");
    expect(useE2EStore.getState().ring?.master.id).toBe(via.anchor);
    expect(auth.ceremonies.length).toBe(touches);
    expect(useE2EStore.getState().generation).toBe(generation + 1);
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(K.floorOf(ACCOUNT)).toEqual([via.anchor]);
  });

  it("drops the keys it waits for at a lock, and when the change no longer waits", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    await useE2EStore.getState().load(ACCOUNT);
    const made = async () => {
      const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
      await t.prepare(await K.E2EApi.view());
      await t.firstTouch();
      await t.secondTouch();
      return (await t.send(true)).via;
    };
    useE2EStore.getState().awaitKeys(await made());
    useE2EStore.getState().lock(IDLE_WORDS);                       // the tab locked before the hold ended
    expect(useE2EStore.getState().waiting).toBeNull();
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);
    expect(stateNow()).toBe("locked");
    // cancelled while this tab waited for it: nothing waits any more
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    const again = new K.TurnOn(ACCOUNT, true, CLASSICAL);
    await again.prepare(await K.E2EApi.view());
    await again.firstTouch();
    await again.secondTouch();
    useE2EStore.getState().awaitKeys((await again.send(true)).via);
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).not.toBeNull();         // starting again waits a day
    server.pending = null;
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(stateNow()).toBe("locked");
    // the keys current before it are current still: no other keys took effect, and nothing more is said
    expect(useE2EStore.getState().why).toBe(IDLE_WORDS);
  });

  /** Encryption turned on with `id`'s passkey, its hold not ended: what unlocks the keys it made. */
  const turnOnWith = async (id: string) => {
    const t = new K.TurnOn(ACCOUNT, false, CLASSICAL);
    await t.prepare(await K.E2EApi.view());
    auth.choose = (allowed) => (allowed.includes(id) ? id : allowed[0]);
    await t.firstTouch();
    await t.secondTouch();
    return (await t.send(true)).via;
  };

  it("waits on while another passkey's turning-on waits in the place of its own, and says so when other keys take effect", async () => {
    for (const [id, name] of [["credPHON", "Phone"], ["credTABL", "Tablet"]]) { server.addPasskey(id, name); auth.add(id); }
    await useE2EStore.getState().load(ACCOUNT);
    const mine = await turnOnWith("credPHON");
    useE2EStore.getState().awaitKeys(mine);
    expect(useE2EStore.getState().waiting?.from).toBeNull();           // no keys were current
    await useE2EStore.getState().load(ACCOUNT);
    expect(K.turnOnInPlace(useE2EStore.getState().view!.pending, mine.anchor)).toBe(false);
    const theirs = await turnOnWith("credTABL");                       // the tablet's holder, in another browser
    await useE2EStore.getState().load(ACCOUNT);
    const s = useE2EStore.getState();
    expect(s.view!.passkeys.find((p) => p.passkey_id === s.view!.pending!.made_by)?.name).toBe("Tablet");
    expect(K.turnOnInPlace(s.view!.pending, mine.anchor)).toBe(true);
    expect(s.waiting?.anchor).toBe(mine.anchor);                       // it waits on, to say what came of it
    expect(s.why).toBe("");
    server.settle();
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().view!.mk_id).toBe(theirs.anchor);
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(useE2EStore.getState().ring).toBeNull();
    expect(useE2EStore.getState().why).toBe(K.KEYS_MADE_ELSEWHERE);
    expect(stateNow()).toBe("locked");
  });

  it("waits no more, saying nothing, when a change of another kind waits in the place of its own", async () => {
    server.addPasskey("credPHON", "Phone");
    auth.add("credPHON");
    await useE2EStore.getState().load(ACCOUNT);
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON"));
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).not.toBeNull();
    server.pending = { id: "hold-9", change: "rotate", takes_effect_at: server.now + 180, apply: () => {} };
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(useE2EStore.getState().why).toBe("");
  });

  it("says nothing when the turning-on it made is cancelled before any keys were current", async () => {
    server.addPasskey("credPHON", "Phone");
    auth.add("credPHON");
    await useE2EStore.getState().load(ACCOUNT);
    useE2EStore.getState().awaitKeys(await turnOnWith("credPHON"));
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).not.toBeNull();
    server.pending = null;
    await useE2EStore.getState().load(ACCOUNT);
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(useE2EStore.getState().why).toBe("");
    expect(stateNow()).toBe("off");
  });

  it("is unsupported in a browser without X25519", async () => {
    expect(stateOf({ supported: false, ring: null, view: null })).toBe("unsupported");
    const importKey = crypto.subtle.importKey.bind(crypto.subtle);
    vi.spyOn(crypto.subtle, "importKey").mockImplementation((...a: Parameters<SubtleCrypto["importKey"]>) =>
      ((a[2] as { name?: string })?.name === "X25519" ? Promise.reject(new DOMException("no", "NotSupportedError")) : importKey(...a)) as ReturnType<SubtleCrypto["importKey"]>);
    const seen = { current: null as E2E | null };
    render(<E2EProvider><Probe seen={seen} /></E2EProvider>);
    await vi.waitFor(() => expect(seen.current?.state).toBe("unsupported"));
  });
});

describe("the provider at the page's root", () => {
  it("reads the account's keys once signed in, and the hook says so", async () => {
    await turnedOn();
    signedIn();
    const seen = { current: null as E2E | null };
    render(<E2EProvider><Probe seen={seen} /></E2EProvider>);
    await vi.waitFor(() => expect(seen.current?.state).toBe("locked"));
    expect(seen.current?.on).toBe(true);
    await act(() => seen.current!.unlock());
    expect(seen.current?.state).toBe("unlocked");
    act(() => seen.current!.lock());
    expect(seen.current?.state).toBe("locked");
  });

  it("locks after 30 minutes without the person's own input; a page's own events do not count", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    await turnedOn();
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    act(() => { vi.advanceTimersByTime(IDLE_MS - 60000); });
    expect(stateNow()).toBe("unlocked");
    fireEvent.keyDown(window, { key: "a" });                  // made by a script: not the person's
    fireEvent.pointerDown(window);
    act(() => { vi.advanceTimersByTime(90000); });
    expect(stateNow()).toBe("locked");
    expect(useE2EStore.getState().why).toBe(IDLE_WORDS);
  });

  it("keeps the keys while the person's own input comes", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.spyOn(input, "isPerson").mockReturnValue(true);
    await turnedOn();
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    act(() => { vi.advanceTimersByTime(IDLE_MS - 60000); });
    fireEvent.keyDown(window, { key: "a" });
    act(() => { vi.advanceTimersByTime(IDLE_MS - 60000); });
    expect(stateNow()).toBe("unlocked");
    act(() => { vi.advanceTimersByTime(120000); });
    expect(stateNow()).toBe("locked");
  });

  it("locks at sign-out, and tells every other tab to lock", async () => {
    await turnedOn();
    signedIn();
    const other = new BroadcastChannel(CHANNEL);
    channels.push(other);
    const heard: unknown[] = [];
    other.onmessage = (m) => heard.push(m.data);
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    act(() => useStore.getState().signOut());
    expect(useE2EStore.getState().ring).toBeNull();
    expect(useE2EStore.getState().why).toBe(SIGNED_OUT_WORDS);
    await vi.waitFor(() => expect(heard).toEqual([{ type: "lock" }]));
  });

  it("locks when another tab signs out", async () => {
    await turnedOn();
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    const other = new BroadcastChannel(CHANNEL);
    channels.push(other);
    other.postMessage({ type: "lock" });
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    expect(useE2EStore.getState().why).toBe(SIGNED_OUT_WORDS);
  });

  it("says nothing to the other tabs when a page opens signed out", async () => {
    const other = new BroadcastChannel(CHANNEL);
    channels.push(other);
    const heard: unknown[] = [];
    other.onmessage = (m) => heard.push(m.data);
    render(<E2EProvider />);
    await new Promise((r) => setTimeout(r, 50));
    expect(heard).toEqual([]);
  });

  it("follows a rotation made elsewhere every minute, with the key it holds and no touch", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.spyOn(input, "isPerson").mockReturnValue(true);
    await turnedOn();
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    const elsewhere = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Rotate(elsewhere, await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const touches = auth.ceremonies.length;
    expect(useE2EStore.getState().ring?.keys.size).toBe(1);
    act(() => { vi.advanceTimersByTime(60000); });
    await vi.waitFor(() => expect(useE2EStore.getState().ring?.keys.size).toBe(2));
    expect(auth.ceremonies.length).toBe(touches);
  });

  /** This tab unlocked; another tab revokes and follows, its ring (`moved`) raising this browser's floor; the rows this
   *  tab opened (`opened`) and the rows after (`after`). */
  async function anotherTabMoves() {
    await turnedOn();
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    const opened = snapshot();
    const other = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    const r = new K.Revoke(other, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const moved = await K.follow(other, await K.E2EApi.view());
    return { opened, after: snapshot(), moved };
  }

  const floorRaised = () => act(() => { window.dispatchEvent(new StorageEvent("storage", { key: K.floorKey(ACCOUNT) })); });

  it("follows at once, with no touch, when another tab raises this browser's floor past its keys", async () => {
    const { moved } = await anotherTabMoves();
    const touches = auth.ceremonies.length;
    await K.raiseFloor(ACCOUNT, moved);
    floorRaised();
    await vi.waitFor(() => expect(useE2EStore.getState().ring?.master.id).toBe(moved.master.id));
    expect(auth.ceremonies.length).toBe(touches);
  });

  it("locks when another tab raises this browser's floor past its keys and the server serves this one exactly the rows it opened", async () => {
    const { opened, moved } = await anotherTabMoves();
    Object.assign(server, opened);
    await K.raiseFloor(ACCOUNT, moved);
    floorRaised();
    await vi.waitFor(() => expect(useE2EStore.getState().ring).toBeNull());
    expect(useE2EStore.getState().why).toBe(K.ROLLED_BACK);
  });

  it("follows a statement made elsewhere though no key changed with it, so what it holds is checked against what the account states", async () => {
    await turnedOn();
    await useE2EStore.getState().load(ACCOUNT);
    await useE2EStore.getState().unlock();
    const before = useE2EStore.getState().ring!;
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();                                                 // an e2e-off: no key row changes
    await useE2EStore.getState().load(ACCOUNT);
    const after = useE2EStore.getState().ring!;
    expect(after).not.toBe(before);
    expect(after.master.id).toBe(before.master.id);
  });

  it("locks, saying why, when a change made elsewhere leaves this tab's key nothing to open", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    vi.spyOn(input, "isPerson").mockReturnValue(true);
    await turnedOn("credAAAA");
    signedIn();
    render(<E2EProvider />);
    await vi.waitFor(() => expect(stateNow()).toBe("locked"));
    await act(() => useE2EStore.getState().unlock());
    // another passkey enrolled, then the one this tab holds removed, elsewhere
    const elsewhere = await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT);
    server.addPasskey("credBBBB", "Key", "hardware");
    auth.add("credBBBB");
    const v = await K.E2EApi.view();
    const e = new K.Enrol(elsewhere, v, v.passkeys.find((p) => p.credential_id === "credBBBB")!);
    await e.prepare();
    await e.firstTouch();
    await e.secondTouch();
    await e.confirmAndSend();                                // A's touch confirms it, the passkey that unlocks the keys
    server.settle();
    const r = new K.Revoke(await K.follow(elsewhere, await K.E2EApi.view()), await K.E2EApi.view(), { remove: ["pk-credAAAA"] });
    await r.prepare();
    auth.choose = () => "credBBBB";
    await r.signAndSend();
    server.settle();
    act(() => { vi.advanceTimersByTime(60000); });
    await vi.waitFor(() => expect(useE2EStore.getState().ring).toBeNull());
    expect(useE2EStore.getState().why).toBe(K.NO_GRANT);
    // a reason no view gives is no view's verdict: the next read leaves it, until an unlock
    await act(() => useE2EStore.getState().load(ACCOUNT));
    expect([useE2EStore.getState().why, useE2EStore.getState().verdict]).toEqual([K.NO_GRANT, false]);
  });
});

describe("post-quantum keys in the tab's state", () => {
  const load = () => useE2EStore.getState().load(ACCOUNT);
  const putAway = (via: K.Via) => {
    const priv = via.kind === "credential" ? via.ck : via.rk;
    try {
      C.hybridPublic(priv.key as C.Hybrid);
      return false;
    } catch (e) {
      return e instanceof Error && e.message === C.PUT_AWAY;
    }
  };
  /** A classical account's keys unlocked in this tab, and its upgrade signed here, waiting: what unlocks the new keys. */
  async function upgradeWaiting() {
    await turnedOn();
    await load();
    await useE2EStore.getState().unlock();
    const u = new K.Upgrade(useE2EStore.getState().ring!, await K.E2EApi.view(), false);
    await u.useCode(u.codeText().split(" ")[u.group]);
    await u.prepare();
    const { via } = await u.signAndSend();
    useE2EStore.getState().awaitKeys(via, true);
    await load();
    return via;
  }

  it("opens the upgrade's keys once it takes effect, in place of the classical keys held, as a new generation", async () => {
    const via = await upgradeWaiting();
    const classical = useE2EStore.getState().ring!;
    expect(classical.pq).toBe(false);                                   // nothing changes while it waits
    const generation = useE2EStore.getState().generation;
    server.settle();
    await load();
    const s = useE2EStore.getState();
    expect([s.why, s.waiting, s.ring?.pq, s.ring?.master.id]).toEqual(["", null, true, via.anchor]);
    expect(s.generation).toBe(generation + 1);                          // every text opened again, with the new keys
    expect(putAway(via)).toBe(false);                                   // what unlocks the tab now
  });

  it("refuses the upgrade's keys where they do not lead back to the keys held, and locks saying why", async () => {
    await upgradeWaiting();
    server.settle();
    server.chain = [];                                                  // its chain link left out
    await load();
    const s = useE2EStore.getState();
    expect([s.ring, s.waiting]).toEqual([null, null]);
    expect(s.why).not.toBe("");
  });

  it("puts away what would have unlocked the upgrade's keys once it is cancelled, and keeps the keys held", async () => {
    const via = await upgradeWaiting();
    const held = useE2EStore.getState().ring;
    server.pending = null;                                              // cancelled
    await load();
    expect(useE2EStore.getState().waiting).toBeNull();
    expect(putAway(via)).toBe(true);
    expect(useE2EStore.getState().ring).toBe(held);
  });

  it("opens the keys it made by starting again in place of older keys it holds, which they do not lead back to", async () => {
    await turnedOn();
    await load();
    await useE2EStore.getState().unlock();
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await load();
    const again = new K.TurnOn(ACCOUNT, true);
    await again.prepare(await K.E2EApi.view());
    await again.firstTouch();
    await again.secondTouch();
    const { via } = await again.send(true);
    useE2EStore.getState().awaitKeys(via);
    await load();
    expect(useE2EStore.getState().ring?.pq).toBe(false);               // the older keys, while it waits
    server.settle();
    await load();
    const s = useE2EStore.getState();
    expect([s.why, s.waiting, s.ring?.pq, s.ring?.master.id]).toEqual(["", null, true, via.anchor]);
  });

  it("judges again, from each view it reads, a lock's reason that was the view's verdict of being older: none once the keys served are not older, the new verdict in its place", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
      recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
    const none = rows();                                                // as a backup made before encryption was turned on
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
    const one = rows();                                                 // its first statement
    server.rotateEvery = 0;
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const two = rows();                                                 // and a rotation's
    const why = () => useE2EStore.getState().why;
    const served = async (kept: Record<string, unknown>) => {
      Object.assign(server, JSON.parse(JSON.stringify(kept)));
      await load();
      return why();
    };
    /** The tab unlocked (this browser sees two statements, post-quantum), then the server put back to no keys: it locks
     *  with the view's verdict. */
    const lockedOverNone = async () => {
      await served(two);
      await useE2EStore.getState().unlock();
      expect(await served(none)).toBe(K.ROLLED_BACK);
      expect(useE2EStore.getState().ring).toBeNull();
    };
    await lockedOverNone();
    expect(await served(two)).toBe("");                                 // the same keys back: nothing left of it
    expect(await served(none)).toBe("");                                // the view says it now, not a lock
    await lockedOverNone();
    expect(await served({ ...two, pq: false })).toBe(K.ROLLED_BACK_CLASSICAL);   // a classical copy: its own words
    expect(await served(two)).toBe("");
    await lockedOverNone();
    expect(await served(one)).toBe(K.ROLLED_BACK);                      // fewer statements: as keys put back
    expect(await served(one)).toBe(K.ROLLED_BACK);
    expect(await served(two)).toBe("");
  });

  it("puts away the keys it waited for when it waits for others", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    await load();
    const made = async () => {
      const t = new K.TurnOn(ACCOUNT);
      await t.prepare(await K.E2EApi.view());
      await t.firstTouch();
      await t.secondTouch();
      return (await t.send(true)).via;
    };
    const first = await made();
    useE2EStore.getState().awaitKeys(first);
    const second = await made();
    useE2EStore.getState().awaitKeys(second);
    expect([putAway(first), putAway(second)]).toEqual([true, false]);
  });

  it("opens a text again with the keys it holds now when those it began with were put away as it followed a change", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
    await load();
    await useE2EStore.getState().unlock();
    const first = useE2EStore.getState().ring!;
    const slot = eventSlot(ACCOUNT, "l", "step", null, null, "c1");
    const env = await C.sealEnvelope2(C.unb64u(server.contentKeys.at(-1)!.public_key)!, C.utf8('{"text":"hi"}'), slot);
    const next = await K.follow(first, await K.E2EApi.view());
    const opening = openSealed(env, slot);
    // the tab followed meanwhile: the keys it began with put away, the same generation
    K.forgetRing(first, false);
    useE2EStore.setState({ ring: next });
    expect(await opening).toEqual({ ok: true, value: { text: "hi" } });
  });

  it("puts away the keys it followed to when another read moved the tab on meanwhile, but what unlocks them both", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
    await load();
    await useE2EStore.getState().unlock();
    const slot = eventSlot(ACCOUNT, "l", "step", null, null, "c1");
    const env = await C.sealEnvelope2(C.unb64u(server.contentKeys.at(-1)!.public_key)!, C.utf8('{"text":"hi"}'), slot);
    // a rotation made elsewhere, which this tab follows; locked while it does
    server.rotateEvery = 0;
    const r = new K.Rotate(useE2EStore.getState().ring!, await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    const real = K.follow;
    let followed: K.Ring | null = null;
    let moved: K.Ring | null = null;
    vi.spyOn(K, "follow").mockImplementation(async (...a) => {
      followed = await real(...a);
      moved = await real(...a);                                          // another read's, held instead
      useE2EStore.setState({ ring: moved });
      return followed;
    });
    await load();
    expect(useE2EStore.getState().ring).toBe(moved);
    expect(await K.openWith(followed!, env, slot)).toEqual({ ok: false, why: K.UNREADABLE });
    expect(await K.openWith(moved!, env, slot)).toEqual({ ok: true, value: { text: "hi" } });
    expect(putAway(moved!.via)).toBe(false);
  });
});

// A reason judged again only from the view load reads and against the floor as it is then; and a reason in the words
// for keys put back kept where the chain may still give it.
describe("a lock's reason that was a view's verdict of being older, judged again", () => {
  const load = () => useE2EStore.getState().load(ACCOUNT);
  const why = () => useE2EStore.getState().why;
  /** The account's key rows and statements as the server holds them, as a copy: what a backup of it holds. */
  const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
    recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
  /** The server holding `kept`, and the view read: the tab's reason after it. */
  const served = async (kept: Record<string, unknown>) => {
    Object.assign(server, JSON.parse(JSON.stringify(kept)));
    await load();
    return why();
  };
  /** A post-quantum account turned on with the laptop's passkey, its hold ended. */
  async function turnedOnPq() {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
  }
  /** Its rows before encryption was turned on (none), with its first statement (one), and with a rotation's (two); the
   *  server serves two. */
  async function rotated() {
    const none = rows();
    await turnedOnPq();
    const one = rows();
    server.rotateEvery = 0;
    const r = new K.Rotate(await K.unlockWithTouch(await K.E2EApi.view(), ACCOUNT), await K.E2EApi.view());
    await r.prepare();
    await r.signAndSend();
    server.settle();
    return { none, one, two: rows() };
  }
  /** Its rows with its first master key and statement (old), and after a revocation this tab followed (now: a new
   *  master key, and a second statement), the tab unlocked; the server serves now. */
  async function revoked() {
    await turnedOnPq();
    await load();
    await useE2EStore.getState().unlock();
    const old = rows();
    const r = new K.Revoke(useE2EStore.getState().ring!, await K.E2EApi.view(), {});
    await r.prepare();
    await r.signAndSend();
    server.settle();
    await load();
    const now = rows();
    expect([useE2EStore.getState().ring?.master.id, K.floorOf(ACCOUNT)]).toEqual([now.roster.mk_id, [now.roster.mk_id]]);
    return { old, now, backIn: { ...old, statements: now.statements } };
  }
  /** Unlocked as the server serves `at`, then the view put back to `older`: the tab locks with that view's verdict. */
  async function lockedBy(at: Record<string, unknown>, older: Record<string, unknown>, verdict: string) {
    await served(at);
    await useE2EStore.getState().unlock();
    expect(await served(older)).toBe(verdict);
    expect(useE2EStore.getState().ring).toBeNull();
  }

  it("judges it again from the view an unlock reads, before any other read: an unlock that fails once it has read keys that are not older leaves no reason behind", async () => {
    const { none, one, two } = await rotated();
    await lockedBy(two, none, K.ROLLED_BACK);
    expect(await served(one)).toBe(K.ROLLED_BACK);                     // then one statement fewer: older still
    // the keys served again, read only by an unlock given something that is not a recovery code
    Object.assign(server, JSON.parse(JSON.stringify(two)));
    await expect(useE2EStore.getState().unlockWithCode("not a code")).rejects.toThrow(K.CODE_FORM);
    const s = useE2EStore.getState();
    expect([s.why, s.view?.statements.length, K.rolledBack(ACCOUNT, s.view!)]).toEqual(["", 2, null]);
    await load();
    await load();
    expect(why()).toBe("");
  });

  it("judges it again once another tab trusted the server's keys: the floor dropped, the same view is older no more", async () => {
    const { one, two } = await rotated();
    await lockedBy(two, one, K.ROLLED_BACK);                           // one statement fewer than this browser saw
    await K.dropFloor(ACCOUNT);                                         // Trust the server's keys, in another tab
    expect(await served(one)).toBe("");
    expect(K.rolledBack(ACCOUNT, useE2EStore.getState().view!)).toBeNull();
  });

  it("records the reason as the view's verdict as the keys are refused, not from the floor as it reads after: another tab trusting while this one reads again before it locks", async () => {
    const { one, two } = await rotated();
    await served(two);
    await useE2EStore.getState().unlock();
    // one statement fewer; as this tab reads the keys once more before locking, another tab trusts the server's keys
    Object.assign(server, JSON.parse(JSON.stringify(one)));
    let reads = 0;
    server.answer = (call) => {
      if (call.path === "/api/ade/e2e" && reads++ === 1) void K.dropFloor(ACCOUNT);
      return null;
    };
    await load();
    server.answer = null;
    const s = useE2EStore.getState();
    expect([reads, s.ring, s.why, K.rolledBack(ACCOUNT, s.view!)]).toEqual([2, null, K.ROLLED_BACK, null]);
    await load();
    expect(why()).toBe("");
  });

  it("keeps it in the words for keys put back where the view gives it no more but names a master key other than the newest this browser opened, which an unlock may still refuse: until that one is served again", async () => {
    const { old, now, backIn } = await revoked();
    expect(await served(old)).toBe(K.ROLLED_BACK);                     // the first master key, and one statement fewer
    // a linked session sends its statements back in: as many as this browser saw, the master key still the first
    expect(await served(backIn)).toBe(K.ROLLED_BACK);
    const v = useE2EStore.getState().view!;
    expect([K.rolledBack(ACCOUNT, v), v.mk_id]).toEqual([null, old.roster.mk_id]);
    // the chain from that master key does not lead back to the newest this browser opened: an unlock says so too
    await expect(useE2EStore.getState().unlock()).rejects.toThrow(K.ROLLED_BACK);
    expect(why()).toBe(K.ROLLED_BACK);
    expect(await served(backIn)).toBe(K.ROLLED_BACK);
    expect(await served(now)).toBe("");
  });

  it("does not keep it where the view names a master key this browser's floor names as keys it made itself (next): an unlock leads back to them", async () => {
    const { old, backIn } = await revoked();
    expect(await served(old)).toBe(K.ROLLED_BACK);
    // as if this browser had made the keys that master key opens (a start again it placed), kept beside its floor
    const kept = JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!);
    localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify({ ...kept, next: old.roster.mk_id }));
    expect(K.floorOf(ACCOUNT)).toEqual([kept.floor, old.roster.mk_id]);
    expect(await served(backIn)).toBe("");
  });

  it("does not keep it where an unlock could not give those words: the view names no master key, which an unlock refuses before any walk", async () => {
    const { old, now } = await revoked();
    expect(await served(old)).toBe(K.ROLLED_BACK);
    // the master key's rows deleted, the credentials and statements kept: keys, as the pane counts them, not older
    expect(await served({ ...now, roster: null, grants: [] })).toBe("");
    const v = useE2EStore.getState().view!;
    expect([K.hasKeys(v), v.mk_id, K.rolledBack(ACCOUNT, v)]).toEqual([true, null, null]);
  });

  it("does not keep it where this browser keeps no master key to lead back to: another tab trusted the server's keys, so an unlock walks to none", async () => {
    const { old, backIn } = await revoked();
    expect(await served(old)).toBe(K.ROLLED_BACK);
    await K.dropFloor(ACCOUNT);
    expect(K.floorOf(ACCOUNT)).toEqual([]);
    expect(await served(backIn)).toBe("");
  });

  it("does not keep the classical words once the view is not classical, whatever master key it names: they are a view's alone, never the chain's", async () => {
    const { now, backIn } = await revoked();
    expect(await served({ ...now, pq: false })).toBe(K.ROLLED_BACK_CLASSICAL);
    expect(await served(backIn)).toBe("");
    expect(useE2EStore.getState().view?.mk_id).not.toBe(now.roster.mk_id);
  });

  it("records as a view's verdict the refusal of keys this tab made, as they take effect: judged again from each view read", async () => {
    await turnedOnPq();
    await load();
    await useE2EStore.getState().unlock();
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await load();
    useE2EStore.getState().lock();                                     // the person's Lock: no keys held
    // started again from this tab, waiting on its hold
    const again = new K.TurnOn(ACCOUNT, true);
    await again.prepare(await K.E2EApi.view());
    await again.firstTouch();
    await again.secondTouch();
    useE2EStore.getState().awaitKeys((await again.send(true)).via);
    await load();
    expect(useE2EStore.getState().waiting).not.toBeNull();
    // as if another tab of this browser had seen more statements than the keys this tab made state
    localStorage.setItem(K.floorKey(ACCOUNT), JSON.stringify({ ...JSON.parse(localStorage.getItem(K.floorKey(ACCOUNT))!), seq: 99 }));
    server.settle();
    await load();
    const s = useE2EStore.getState();
    expect([s.why, s.ring, s.waiting]).toEqual([K.ROLLED_BACK, null, null]);
    await K.dropFloor(ACCOUNT);                                         // that tab trusts the server's keys
    await load();
    expect(why()).toBe("");
  });
});

describe("whether the hook says encryption is on, for the texts the page marks as sent in the clear", () => {
  const load = () => useE2EStore.getState().load(ACCOUNT);
  const rows = () => JSON.parse(JSON.stringify({ statements: server.statements, credentials: server.credentials, grants: server.grants,
    recovery: server.recovery, roster: server.roster, contentKeys: server.contentKeys, chain: server.chain, pq: server.pq }));
  const served = (kept: Record<string, unknown>) => act(async () => {
    Object.assign(server, JSON.parse(JSON.stringify(kept)));
    await load();
  });
  /** The view's word, and the hook's. */
  const said = (seen: { current: E2E | null }) => [!!useE2EStore.getState().view?.on, seen.current?.on];
  async function turnedOnPq() {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
  }

  it("says on where the server serves no keys after this browser saw them, though the view says off, until this site's data is cleared", async () => {
    const before = rows();                                              // a copy from before encryption was turned on
    await turnedOnPq();
    const seen = { current: null as E2E | null };
    render(<Probe seen={seen} />);
    await served(rows());
    await act(() => useE2EStore.getState().unlock());
    expect(said(seen)).toEqual([true, true]);
    await served(before);
    expect([seen.current?.state, ...said(seen)]).toEqual(["off", false, true]);
    // trusting the server's keys drops the master key this browser opened, not what it saw post-quantum
    await act(() => K.dropFloor(ACCOUNT));
    await served(before);
    expect([K.floorOf(ACCOUNT), ...said(seen)]).toEqual([[], false, true]);
    localStorage.clear();                                               // this site's data cleared: the server's word
    await served(before);
    expect(said(seen)).toEqual([false, false]);
  });

  it("says on in a tab opened after the server was put back, as a reload opens it: what this browser kept decides, not a reason a tab recorded", async () => {
    const before = rows();
    await turnedOnPq();
    await served(rows());
    await act(() => useE2EStore.getState().unlock());
    // the server put back, and the page opened again: the store as a page opens it, this browser's storage kept
    Object.assign(server, JSON.parse(JSON.stringify(before)));
    cleanup();
    useE2EStore.setState(useE2EStore.getInitialState());
    const seen = { current: null as E2E | null };
    render(<Probe seen={seen} />);
    await served(before);
    const s = useE2EStore.getState();
    expect([s.why, s.ring, K.rolledBack(ACCOUNT, s.view!), ...said(seen)]).toEqual(["", null, K.ROLLED_BACK, false, true]);
  });

  it("says on where the server's keys say off but hold fewer statements than this browser saw: turned off, then started again", async () => {
    await turnedOnPq();
    await load();
    await useE2EStore.getState().unlock();
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await load();
    const turnedOff = rows();                                           // the keys kept, and off
    useE2EStore.getState().lock();
    const again = new K.TurnOn(ACCOUNT, true);
    await again.prepare(await K.E2EApi.view());
    await again.firstTouch();
    await again.secondTouch();
    useE2EStore.getState().awaitKeys((await again.send(true)).via);
    server.settle();
    await load();
    expect(useE2EStore.getState().ring).not.toBeNull();                 // the keys it made, taken effect and held
    const seen = { current: null as E2E | null };
    render(<Probe seen={seen} />);
    expect(said(seen)).toEqual([true, true]);
    await served(turnedOff);
    const v = useE2EStore.getState().view!;
    expect([K.hasKeys(v), K.rolledBack(ACCOUNT, v), ...said(seen)]).toEqual([true, K.ROLLED_BACK, false, true]);
  });

  it("says on where the keys served are classical where this browser saw them post-quantum, though they say off", async () => {
    await turnedOn();                                                   // classical
    await load();
    await useE2EStore.getState().unlock();
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await load();
    const classicalOff = rows();
    useE2EStore.getState().lock();
    const again = new K.TurnOn(ACCOUNT, true);                          // started again, post-quantum
    await again.prepare(await K.E2EApi.view());
    await again.firstTouch();
    await again.secondTouch();
    useE2EStore.getState().awaitKeys((await again.send(true)).via);
    server.settle();
    await load();
    expect(useE2EStore.getState().ring?.pq).toBe(true);
    const seen = { current: null as E2E | null };
    render(<Probe seen={seen} />);
    await served(classicalOff);
    const v = useE2EStore.getState().view!;
    expect([K.rolledBack(ACCOUNT, v), ...said(seen)]).toEqual([K.ROLLED_BACK_CLASSICAL, false, true]);
  });

  it("says off as the view says where the account turned encryption off, its keys kept", async () => {
    await turnedOnPq();
    const seen = { current: null as E2E | null };
    render(<Probe seen={seen} />);
    await served(rows());
    expect(said(seen)).toEqual([true, true]);
    await act(() => useE2EStore.getState().unlock());
    const off = new K.TurnOff(await K.E2EApi.view(), ACCOUNT);
    await off.prepare();
    await off.signAndSend();
    server.settle();
    await served(rows());
    expect([K.rolledBack(ACCOUNT, useE2EStore.getState().view!), ...said(seen)]).toEqual([null, false, false]);
  });
});
