import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, ApiError, type SessionRecord } from "../api";
import { useStore } from "../store";
import { ShipDialog } from "./ShipDialog";

afterEach(() => { cleanup(); vi.restoreAllMocks(); useStore.setState(useStore.getInitialState()); });

const rec = { id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", base: "abc", task: "Fix it",
  created_at: 1, running: false, finished: null, exit_code: 0, ended_at: 2, chat: null } as SessionRecord;
const ship = () => screen.getByRole("button", { name: /Commit, push and open a pull request|Open the pull request/ });

describe("shipping a session", () => {
  it("says before anything runs that GitHub is not signed in, and waits for it", () => {
    useStore.setState({ github: { installed: true, user: null, repos: [], capped: false } });
    const finish = vi.spyOn(Api, "finish");
    render(<ShipDialog rec={rec} onClose={() => {}} onConnections={() => {}} />);
    expect(screen.getByRole("status").textContent).toMatch(/GitHub is not signed in here/);
    expect((ship() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(ship());
    expect(finish).not.toHaveBeenCalled();
  });

  it("after a push whose pull request was not opened, says so, and a second try only opens it", async () => {
    useStore.setState({ github: { installed: true, user: "ana", repos: [], capped: false }, refresh: async () => {} });
    const finish = vi.spyOn(Api, "finish").mockResolvedValue({ status: "verified", committed: true, files: 1, rule: "all-pass" });
    const shipping = vi.spyOn(Api, "ship")
      .mockRejectedValueOnce(new ApiError(400, "Pushed pp/0123456789ab to origin, but the pull request was not opened: GitHub is busy."))
      .mockResolvedValueOnce({ url: "https://github.com/acme/demo/pull/7" } as never);
    render(<ShipDialog rec={rec} onClose={() => {}} onConnections={() => {}} />);
    fireEvent.click(ship());
    expect(await screen.findByText(/Pushed pp\/0123456789ab to origin, but the pull request was not opened/)).toBeTruthy();
    expect(ship().textContent).toContain("Open the pull request");
    fireEvent.click(ship());
    await vi.waitFor(() => expect(shipping).toHaveBeenCalledTimes(2));
    expect(finish).toHaveBeenCalledTimes(1);                     // committed and verified once
  });
});
