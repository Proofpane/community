import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Api, type PullRequest, type SessionRecord } from "../api";
import { PullRequestButton, PullReviewButton, ReviewDialog, repoOf } from "./PullRequest";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const rec = (over: Partial<SessionRecord> = {}) =>
  ({ id: "0123456789ab", program: "claude", repo: "demo", branch: "pp/0123456789ab", task: "Review pull request #7", created_at: 1, running: true, pull: 7, ...over }) as SessionRecord;

const pr = (over: Partial<PullRequest> = {}): PullRequest => ({
  session: "0123456789ab", url: "https://github.com/acme/demo/pull/7", number: 7, title: "Add the settings page", state: "OPEN", draft: false,
  mergeable: "MERGEABLE", merge_state: "CLEAN", review: "REVIEW_REQUIRED", merged_at: null, base: "main",
  checks: { passed: 2, failed: 0, running: 0, items: [] }, author: "ana", ...over,
});

describe("the person's own review of a pull request a session started from", () => {
  it("is offered only for a session started from one and not shipped", () => {
    const { rerender } = render(<PullReviewButton rec={rec()} />);
    expect(screen.getByTitle("Approve #7, ask for changes, or comment, on GitHub")).toBeTruthy();
    rerender(<PullReviewButton rec={rec({ pull: undefined })} />);
    expect(screen.queryByTitle(/Approve #/)).toBeNull();
    rerender(<PullReviewButton rec={rec({ shipped: { url: "https://github.com/acme/demo/pull/8", at: 1 } } as Partial<SessionRecord>)} />);
    expect(screen.queryByTitle(/Approve #/)).toBeNull();
  });

  it("shows the pull request as GitHub has it, and approves that one, as the person", async () => {
    vi.spyOn(Api, "pullRequest").mockResolvedValueOnce(pr()).mockResolvedValue(pr({ review: "APPROVED" }));
    const reviewed = vi.spyOn(Api, "reviewPr").mockResolvedValue({ reviewed: "approve", url: pr().url });
    render(<ReviewDialog rec={rec()} onClose={() => {}} />);
    expect(await screen.findByText("Add the settings page")).toBeTruthy();
    expect(screen.getByText("acme/demo")).toBeTruthy();
    expect(screen.getByText(/by ana/)).toBeTruthy();
    // asking for changes, or commenting, needs words; an approval does not
    expect((screen.getByText("Request changes") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByText("Comment") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("Approve"));
    expect(await screen.findByText("Approved on GitHub.")).toBeTruthy();
    expect(reviewed).toHaveBeenCalledWith("0123456789ab", "approve", "", "https://github.com/acme/demo/pull/7");
    expect(screen.getByText("Approved")).toBeTruthy();
  });

  it("asks for changes in the person's words, and says what GitHub said when it refuses", async () => {
    vi.spyOn(Api, "pullRequest").mockResolvedValue(pr());
    const reviewed = vi.spyOn(Api, "reviewPr").mockRejectedValueOnce(new Error("gh could not review the pull request: Can not request changes on your own pull request"))
      .mockResolvedValue({ reviewed: "request_changes", url: pr().url });
    render(<ReviewDialog rec={rec()} onClose={() => {}} />);
    await screen.findByText("Add the settings page");
    fireEvent.change(screen.getByLabelText("Your review"), { target: { value: "  Please add a test for the empty form.  " } });
    fireEvent.click(screen.getByText("Request changes"));
    expect(await screen.findByText(/Can not request changes on your own pull request/)).toBeTruthy();
    fireEvent.click(screen.getByText("Request changes"));
    expect(await screen.findByText("Changes requested on GitHub.")).toBeTruthy();
    expect(reviewed).toHaveBeenLastCalledWith("0123456789ab", "request_changes", "Please add a test for the empty form.", pr().url);
  });
});

describe("the repository a pull request is in", () => {
  it("is owner/name on GitHub, and names the host on another", () => {
    expect(repoOf("https://github.com/acme/demo/pull/7")).toBe("acme/demo");
    expect(repoOf("https://ghe.example.com/acme/demo/pull/7")).toBe("ghe.example.com/acme/demo");
    expect(repoOf("not an address")).toBe("not an address");
  });

  it("is named beside Merge, so the person sees whose pull request they merge", async () => {
    const url = "https://github.com/elsewhere/other/pull/7";
    vi.spyOn(Api, "pullRequest").mockResolvedValue(pr({ url }));
    render(<PullRequestButton rec={rec({ pull: undefined, shipped: { url, at: 1, commits: 1 } })} />);
    await screen.findByText("#7");
    fireEvent.click(screen.getByRole("button", { name: /Pull request/ }));
    const note = await screen.findByText(/^Merges in/);
    expect(note.textContent).toBe("Merges in elsewhere/other, with the workspace's GitHub sign-in. The branches stay.");
    expect(screen.getByRole("button", { name: "Merge" })).toBeTruthy();
  });
});
