import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type SessionAsk, type SessionRecord } from "../api";
import { useStore } from "../store";
import { Approvals } from "./Approvals";

afterEach(() => {
  cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); useStore.setState(useStore.getInitialState());
});

const ask = (over: Partial<SessionAsk> = {}): SessionAsk => ({
  id: "a1b2c3d4e5f6", from: "0123456789ab", repo: "demo", label: "acme/demo", program: "codex",
  task: "Write the parser's tests.\nRun them.", base: "main", at: 1,
  by: { id: "0123456789ab", title: "Fix the parser", program: "claude" }, ...over,
});

describe("a session an agent asked for, in Approvals", () => {
  it("shows who asked, for which agent and base, and the task as text, and starts only when the person starts it", async () => {
    const started = vi.spyOn(Api, "startAsk").mockResolvedValue({ id: "fedcba987654" } as SessionRecord);
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {} });
    const onOpen = vi.fn();
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask({ task: "<b>not markup</b>\nline two" })]} onClose={() => {}} onOpen={onOpen} />);
    expect(screen.getByText("Fix the parser · acme/demo")).toBeTruthy();
    expect(screen.getByText(/asks for a new Codex session/)).toBeTruthy();
    expect(screen.getByText("main")).toBeTruthy();
    expect(screen.getByText("<b>not markup</b> line two", { normalizer: (t) => t.replace(/\s+/g, " ") })).toBeTruthy();
    expect(screen.queryByText("Nothing is waiting for you.")).toBeNull();
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("fedcba987654"));
    expect(started).toHaveBeenCalledWith("a1b2c3d4e5f6");
  });

  it("is refused with one click, and the session that asked can be opened", async () => {
    const refused = vi.spyOn(Api, "refuseAsk").mockResolvedValue({ on: true, asks: [] });
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {} });
    const onOpen = vi.fn();
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask()]} onClose={() => {}} onOpen={onOpen} />);
    fireEvent.click(screen.getByText("Refuse"));
    await vi.waitFor(() => expect(refused).toHaveBeenCalledWith("a1b2c3d4e5f6"));
    fireEvent.click(screen.getByTitle("Open the session that asked"));
    expect(onOpen).toHaveBeenCalledWith("0123456789ab");
  });
});

describe("an ask's agent and length", () => {
  it("names the agent as the workspace starts it, Kimi Code for kimi, and says how long a long task is", () => {
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {} });
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask({ program: "kimi", task: Array.from({ length: 20 }, (_, i) => `line ${i}`).join("\n") })]}
      onClose={() => {}} onOpen={() => {}} />);
    expect(screen.getByText(/asks for a new Kimi Code session/)).toBeTruthy();
    expect(screen.queryByText(/Kimi K3/)).toBeNull();
    expect(screen.getByText("20 lines: read them all before you start it.")).toBeTruthy();
  });
});

describe("the effort of a session an agent asked for", () => {
  /** Every POST the page makes, by its address and its body as sent: what reaches the app, not what Api was called
   *  with. */
  const posts = () => {
    const calls: { url: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
      if (init.method === "POST") calls.push({ url: String(url), body: JSON.parse(String(init.body)) });
      return new Response(JSON.stringify({ id: "fedcba987654" }), { status: 200 });
    }));
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {} });
    return calls;
  };
  const START = "/api/ade/workspace/asks/a1b2c3d4e5f6/start";
  /** The drawer with one ask in it; what it opens goes to the onOpen returned. */
  const show = (over: Partial<SessionAsk>) => {
    const onOpen = vi.fn();
    render(<Approvals records={[]} requests={[]} sessionAsks={[ask(over)]} onClose={() => {}} onOpen={onOpen} />);
    return onOpen;
  };

  it("offers a Claude Code ask's effort set to what it asked, and starts the session with what is chosen", async () => {
    const calls = posts();
    const onOpen = show({ program: "claude", effort: "max" });
    expect(screen.getByText(/It asks for Max effort, which you may change/)).toBeTruthy();
    const select = screen.getByLabelText("Effort") as HTMLSelectElement;
    expect(select.value).toBe("max");
    // the installed Claude Code's efforts, as a chat's are offered, its own default first
    expect(Array.from(select.options).map((o) => o.value))
      .toEqual(["", "low", "medium", "high", "xhigh", "max", "ultracode"]);
    fireEvent.change(select, { target: { value: "low" } });
    // what it shows is what Start sends
    expect(select.value).toBe("low");
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("fedcba987654"));
    expect(calls).toEqual([{ url: START, body: { effort: "low" } }]);
  });

  it("starts as asked when nothing is changed, and says what the chosen effort means", async () => {
    const calls = posts();
    const onOpen = show({ program: "claude", effort: "ultracode" });
    expect(screen.getByText("Extra high effort, and Claude may fan out sub-agents on its own.")).toBeTruthy();
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalled());
    expect(calls).toEqual([{ url: START, body: { effort: "ultracode" } }]);
  });

  it("starts on Claude Code's default when the ask names none, and sends no level", async () => {
    const calls = posts();
    const onOpen = show({ program: "claude" });
    expect(screen.getByText(/It names no effort: Claude Code's default/)).toBeTruthy();
    expect((screen.getByLabelText("Effort") as HTMLSelectElement).value).toBe("");
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalled());
    // "" and no effort at all mean the same to the app: Claude Code's default
    expect(calls.map((c) => c.url)).toEqual([START]);
    expect((calls[0].body as { effort?: string }).effort ?? "").toBe("");
  });

  it("sends the default when the person changes an asked effort to it", async () => {
    const calls = posts();
    const onOpen = show({ program: "claude", effort: "max" });
    fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "" } });
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalled());
    expect((calls[0].body as { effort?: string }).effort ?? "").toBe("");
  });

  it("keeps an effort the ask names that the installed Claude Code does not list, shown as itself", () => {
    posts();
    show({ program: "claude", effort: "maximal" });
    const select = screen.getByLabelText("Effort") as HTMLSelectElement;
    expect(select.value).toBe("maximal");
    expect(Array.from(select.options).map((o) => o.textContent)).toContain("maximal");
  });

  it("says what the level chosen means, the person's choice and not the ask's", () => {
    posts();
    show({ program: "claude", effort: "max" });
    const note = "Extra high effort, and Claude may fan out sub-agents on its own.";
    expect(screen.queryByText(note)).toBeNull();
    fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "ultracode" } });
    expect(screen.getByText(note)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "low" } });
    expect(screen.queryByText(note)).toBeNull();
  });

  it("drops the note when the person changes an ask's ultracode to another level", () => {
    posts();
    show({ program: "claude", effort: "ultracode" });
    const note = "Extra high effort, and Claude may fan out sub-agents on its own.";
    expect(screen.getByText(note)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "high" } });
    expect(screen.queryByText(note)).toBeNull();
  });

  it("says what the app answers of an effort the session did not start with", async () => {
    // an agent older than the app takes no effort at Start, and the app's answer says so: the page says it
    const said = "The session started at Claude Code's default effort, not at low, the effort you chose. This " +
      "workspace's agent is older than the app and takes no effort at Start.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: "fedcba987654", effort_notice: said }),
      { status: 200 })));
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {} });
    const onOpen = show({ program: "claude", effort: "max" });
    fireEvent.change(screen.getByLabelText("Effort"), { target: { value: "low" } });
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("fedcba987654"));
    await vi.waitFor(() => expect(useStore.getState().notice?.text).toBe(said));
    expect(useStore.getState().notice?.tone).toBe("wait");
  });

  it("says the app's words of an effort not taken for 15 seconds, as a wait", async () => {
    // the words stay 15 seconds, as a start's notice of an effort not taken does
    const said = "Claude Code no longer offers effort ultracode: the session started at its default.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: "fedcba987654", effort_notice: said }),
      { status: 200 })));
    const say = vi.fn();
    useStore.setState({ refresh: async () => {}, refreshApprovals: async () => {}, say });
    const onOpen = show({ program: "claude", effort: "ultracode" });
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalledWith("fedcba987654"));
    expect(say).toHaveBeenCalledWith(said, "wait", 15000);
  });

  it("says the words of a session that started even when the refresh after it fails", async () => {
    // the session started, so what its start says is said whatever the
    // refresh that follows does, as a start again says it before its caller's refresh
    const said = "The workspace could not read from its Claude Code's help which efforts, or which permission modes, " +
      "it takes, so the session started at Claude Code's default effort, not at max.";
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ id: "fedcba987654", effort_notice: said }),
      { status: 200 })));
    useStore.setState({ refresh: async () => { throw new Error("The workspace is not answering."); },
      refreshApprovals: async () => {} });
    show({ program: "claude", effort: "max" });
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(useStore.getState().error).toBe("The workspace is not answering."));
    expect(useStore.getState().notice?.text).toBe(said);
    expect(useStore.getState().notice?.tone).toBe("wait");
  });

  it("says nothing more when the app's answer to Start says nothing of an effort", async () => {
    posts();
    const onOpen = show({ program: "claude", effort: "max" });
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalled());
    expect(useStore.getState().notice).toBeNull();
  });

  it("offers no effort for an ask of another agent, and its start sends none", async () => {
    const calls = posts();
    const onOpen = show({ program: "codex", effort: "max" });
    expect(screen.queryByLabelText("Effort")).toBeNull();
    expect(screen.queryByText(/effort/i)).toBeNull();
    fireEvent.click(screen.getByText("Start it"));
    await vi.waitFor(() => expect(onOpen).toHaveBeenCalled());
    expect(calls).toEqual([{ url: START, body: {} }]);
  });
});
