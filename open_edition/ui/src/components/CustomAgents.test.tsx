import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type CustomAgent } from "../api";
import { useStore } from "../store";
import { AgentBadge, agentLabel, customMark } from "./bits";
import { CustomAgentsCard } from "./Connections";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("an agent of the person's own", () => {
  it("shows by its name, its mark the name's first letters", () => {
    expect(customMark("Aider")).toBe("AI");
    expect(customMark("goose agent")).toBe("GA");
    expect(customMark(`${String.fromCodePoint(0x1f600)} bot`)).toBe(`${String.fromCodePoint(0x1f600)}B`);
    expect(agentLabel("custom", "Aider")).toBe("Aider");
    expect(agentLabel("custom")).toBe("Your agent");
    render(<AgentBadge kind="custom" name="Amp" />);
    expect(screen.getByTitle("Amp").textContent).toBe("AM");
  });

  it("is added in Connections with its command and how its task reaches it, and forgotten there", async () => {
    let kept: CustomAgent[] = [];
    vi.spyOn(Api, "customAgents").mockImplementation(async () => ({ agents: kept }));
    const added = vi.spyOn(Api, "addCustomAgent").mockImplementation(async (a) => {
      const made = { ...a, id: "0123456789ab" };
      kept = [made];
      return made;
    });
    const forgotten = vi.spyOn(Api, "forgetCustomAgent").mockImplementation(async (id) => { kept = []; return { forgotten: id }; });
    // signed in, as Connections always is: what the store reads is kept only for the account signed in
    useStore.setState({ user: { id: "u1", email: "owner@example.test", can_sign_in_again: true }, customAgents: null, setError: () => {} });
    render(<CustomAgentsCard />);
    expect(await screen.findByText("None yet")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Its name"), { target: { value: " Aider " } });
    fireEvent.change(screen.getByLabelText("Its command"), { target: { value: "aider --model sonnet" } });
    fireEvent.change(screen.getByLabelText("How its task reaches it"), { target: { value: "typed" } });
    fireEvent.click(screen.getByText("Add"));
    expect(await screen.findByText("aider --model sonnet")).toBeTruthy();
    expect(added).toHaveBeenCalledWith({ name: "Aider", command: "aider --model sonnet", task: "typed" });
    expect(screen.getByText("task typed")).toBeTruthy();
    fireEvent.click(screen.getByTitle("Forget Aider"));
    expect(await screen.findByText("None yet")).toBeTruthy();
    expect(forgotten).toHaveBeenCalledWith("0123456789ab");
  });
});
