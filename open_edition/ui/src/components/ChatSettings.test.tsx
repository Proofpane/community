import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SessionRecord } from "../api";
import { useStore } from "../store";
import { ChatSettingsBar } from "./ChatSettings";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState()); });

const record = (chosen: Record<string, unknown>) => ({ id: "s1s2s3s4s5s6", chat: { chosen } }) as unknown as SessionRecord;

describe("a chat's bar", () => {
  it("lets the chat start the agents' runs, and says what that means", async () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      if (init.method === "POST") calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({}), { status: 200 });
    }));
    useStore.setState({ refresh: async () => {} } as never);
    const { rerender } = render(<ChatSettingsBar record={record({})} held={0} onConnectors={() => {}} />);
    const button = screen.getByRole("button", { name: "Agents" });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(screen.queryByText(/may start your agents' runs/)).toBeNull();
    fireEvent.click(button);
    await vi.waitFor(() => expect(calls).toEqual([{ url: "/api/ade/workspace/sessions/s1s2s3s4s5s6/chat/settings", body: { may_dispatch: true } }]));
    rerender(<ChatSettingsBar record={record({ may_dispatch: true })} held={0} onConnectors={() => {}} />);
    expect(screen.getByRole("button", { name: "Agents" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByText(/This chat may start your agents' runs and wait for them/)).toBeTruthy();
    const again = screen.getByRole("button", { name: "Agents" }) as HTMLButtonElement;
    await vi.waitFor(() => expect(again.disabled).toBe(false));          // done with the first
    fireEvent.click(again);
    await vi.waitFor(() => expect(calls[1]).toEqual({ url: "/api/ade/workspace/sessions/s1s2s3s4s5s6/chat/settings", body: { may_dispatch: false } }));
  });
});
