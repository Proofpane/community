// A post-quantum conversation as the page shows it: its messages opened through the key
// served beside them (talkRefOf, useOpened with a conversation), and the session pane asking for them so.
import { act, cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as C from "../e2eCrypto";
import * as K from "../e2eKeys";
import { talkKeySlot, useE2EStore } from "../e2e";
import { FakeAuthenticator, FakeServer } from "../e2eTestkit";
import { useStore } from "../store";
import type { HistoryTalk } from "../api";
import { talkRefOf, talkShown, talkSlotOf, useOpened } from "./Sealed";

const ACCOUNT = "acct-1";
let server: FakeServer;
let auth: FakeAuthenticator;

beforeEach(() => {
  server = new FakeServer();
  auth = new FakeAuthenticator();
  vi.stubGlobal("fetch", server.fetch);
  auth.install();
  useStore.setState({ user: { id: ACCOUNT, email: "a@example.org", can_sign_in_again: true } });
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete (navigator as unknown as { credentials?: unknown }).credentials;
  useE2EStore.setState(useE2EStore.getInitialState());
  useStore.setState(useStore.getInitialState());
  localStorage.clear();
});

const session = { id: "s1", link_id: "l1" };

function Talk({ talk }: { talk: HistoryTalk }) {
  const opener = useOpened(talk.messages.map((m, i) => ({ envelope: m.text, slot: talkSlotOf(talk, i, session, ACCOUNT), talk: talkRefOf(talk, session, ACCOUNT) })));
  return <ul>{talk.messages.map((_, i) => {
    const s = talkShown(talk, i, session, ACCOUNT, opener);
    return <li key={i}>{s.ok ? s.value : `sealed:${s.seal.at}`}</li>;
  })}</ul>;
}

describe("a post-quantum conversation", () => {
  it("opens each message through the conversation's key, and refuses them where the conversation names another key_id (00…00) than its messages'", async () => {
    server.addPasskey("credAAAA", "Laptop");
    auth.add("credAAAA");
    const t = new K.TurnOn(ACCOUNT);
    await t.prepare(await K.E2EApi.view());
    await t.firstTouch();
    await t.secondTouch();
    await t.send(true);
    server.settle();
    await act(async () => {
      await useE2EStore.getState().load(ACCOUNT);
      await useE2EStore.getState().unlock();
    });
    const ring = useE2EStore.getState().ring!;
    const [, held] = [...ring.keys.entries()][0];
    const tkBytes = C.random(32);
    const tk = await C.talkKeyOf(tkBytes);
    const base: HistoryTalk = { messages: [{ who: "owner", text: "", at: 1 }, { who: "session", text: "", at: 2 }], earlier: 0, updated_at: 3 };
    const key = await C.sealTalkKey(held.EK, tkBytes, talkKeySlot(ACCOUNT, "l1", "s1", 2, 0));
    const texts = ["first", "second"];
    const messages = await Promise.all(base.messages.map(async (m, i) => ({ ...m,
      text: await C.sealTalkMessage(tk, C.utf8(JSON.stringify({ text: texts[i] })), talkSlotOf(base, i, session, ACCOUNT)) })));
    const talk: HistoryTalk = { ...base, messages, key, key_id: tk.id };
    const { unmount } = render(<Talk talk={talk} />);
    expect(await screen.findByText("first")).toBeTruthy();
    expect(screen.getByText("second")).toBeTruthy();
    unmount();
    // the conversation names 00…00, its messages and its key name tk.id: neither message is opened under it
    const other = "0".repeat(32);
    expect(other).not.toBe(tk.id);
    render(<Talk talk={{ ...talk, key_id: other }} />);
    expect((await screen.findAllByText("sealed:refused")).length).toBe(2);
    const keySlot = talkKeySlot(ACCOUNT, "l1", "s1", 2, 0);
    expect(await K.openTalkWith(ring, { key, key_id: other }, keySlot, messages[0].text, talkSlotOf(base, 0, session, ACCOUNT)))
      .toEqual({ ok: false, why: K.UNREADABLE });
    expect(await K.openTalkWith(ring, { key, key_id: tk.id }, keySlot, messages[0].text, talkSlotOf(base, 0, session, ACCOUNT)))
      .toEqual({ ok: true, value: { text: "first" } });
  });

  it("is asked for so by the session pane: each message with its conversation", () => {
    const channel = readFileSync(join(__dirname, "Channel.tsx"), "utf8");
    expect(channel).toMatch(/envelope: m\.text, slot: talkSlotOf\(talk!, i, s, account\), talk: talkRefOf\(talk!, s, account\)/);
  });
});
