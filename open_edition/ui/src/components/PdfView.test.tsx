import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PdfView } from "./Preview";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("a PDF in the Files tab", () => {
  it("is shown from memory in a frame, typed as a PDF by the page whatever the reply says", async () => {
    // a reply that calls itself a page: framed as it says, it would run in the app's own origin
    vi.stubGlobal("fetch", vi.fn(async () => new Response("<script>parent.x = 1</script>", { status: 200, headers: { "Content-Type": "text/html" } })));
    const made: Blob[] = [];
    vi.spyOn(URL, "createObjectURL").mockImplementation((b) => { made.push(b as Blob); return "blob:pdf"; });
    vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    render(<PdfView session="0123456789ab" path="docs/report.pdf" size="2 KB" />);
    const frame = await screen.findByTitle("docs/report.pdf, a PDF");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame.getAttribute("src")).toBe("blob:pdf");
    expect(made.map((b) => b.type)).toEqual(["application/pdf"]);
    expect(screen.getByText("2 KB")).toBeTruthy();
  });

  it("says what went wrong in its place", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "The file is larger than 10 MB, the most the page shows." }), { status: 400 })));
    render(<PdfView session="0123456789ab" path="big.pdf" />);
    expect(await screen.findByText("The file is larger than 10 MB, the most the page shows.")).toBeTruthy();
    expect(screen.queryByTitle("big.pdf, a PDF")).toBeNull();
  });
});
