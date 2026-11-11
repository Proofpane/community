import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Licenses, NOTICES, noticesFor, noticeText } from "./Licenses";
import { TitleBar } from "./TitleBar";
import { SignIn } from "./SignIn";

// The licenses of what the app carries (open_edition/NOTICE), shown in the app: the notices each build writes beside
// its page, listed by the build, each read from the page's own files and shown as text.
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function answer(bodies: Record<string, string>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    return url in bodies ? new Response(bodies[url], { status: 200 }) : new Response("not here", { status: 404 });
  });
}

describe("the notices a build carries", () => {
  it("are Proofpane Community's own and its pages' in every build, the Rust crates' in an app, the Python's on a Mac", () => {
    const files = (build: Parameters<typeof noticesFor>[0]) => noticesFor(build).map((n) => n.file);
    expect(files("web")).toEqual(["LICENSE", "NOTICE", "LICENSE-APACHE-2.0", "THIRD_PARTY_LICENSES.txt"]);
    expect(files("phone")).toEqual(["LICENSE", "NOTICE", "LICENSE-APACHE-2.0", "THIRD_PARTY_LICENSES.txt",
      "THIRD_PARTY_LICENSES-rust.txt", "THIRD_PARTY_LICENSES-rust-std.html"]);
    expect(files("desktop")).toEqual(["LICENSE", "NOTICE", "LICENSE-APACHE-2.0", "THIRD_PARTY_LICENSES.txt",
      "THIRD_PARTY_LICENSES-rust.txt", "THIRD_PARTY_LICENSES-rust-std.html", "THIRD_PARTY_LICENSES-python.txt"]);
    expect(NOTICES).toEqual(noticesFor("web"));                       // these tests run as a browser's build
    for (const build of ["web", "phone", "desktop"] as const) expect(noticesFor(build).every((n) => n.label.length > 20)).toBe(true);
  });

  it("shows an HTML one as its text, running nothing in it", () => {
    const html = "<!DOCTYPE html><html><head><title>T</title><style>p { color: red }</style></head><body><h1>Copyright notices</h1>"
      + "\n\n\n\n<p>Licensed under <a href=\"x\">MIT</a>.</p>  \n<script>window.ran = true</script><pre>a\n  b</pre></body></html>";
    const text = noticeText("THIRD_PARTY_LICENSES-rust-std.html", html);
    expect(text).toBe("Copyright notices\n\nLicensed under MIT.\na\n  b");
    expect((window as unknown as { ran?: boolean }).ran).toBeUndefined();
    expect(noticeText("NOTICE", html)).toBe(html);                      // a text one as it is
  });
});

describe("the Licenses dialog", () => {
  it("lists the build's notices, and shows the one chosen, read from the page's own files", async () => {
    const fetched = answer({ "/app/NOTICE": "Proofpane Community\nCopyright 2026 PROOFPANE LIMITED\n" });
    const onClose = vi.fn();
    render(<Licenses onClose={onClose} base="/app/" notices={noticesFor("phone")} />);
    expect(screen.getByRole("dialog", { name: "Licenses" })).toBeTruthy();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(expect.arrayContaining([
      expect.stringContaining("THIRD_PARTY_LICENSES-rust.txt"), expect.stringContaining("THIRD_PARTY_LICENSES-rust-std.html")]));
    fireEvent.click(screen.getByRole("button", { name: /NOTICE$/ }));
    await waitFor(() => expect(screen.getByLabelText("NOTICE").textContent).toBe("Proofpane Community\nCopyright 2026 PROOFPANE LIMITED\n"));
    expect(fetched).toHaveBeenCalledWith("/app/NOTICE");
    fireEvent.click(screen.getByRole("button", { name: "Licenses" }));      // back to the list
    expect(screen.getByRole("dialog", { name: "Licenses" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the Rust standard library's notice as text, and says when one cannot be read", async () => {
    answer({ "/THIRD_PARTY_LICENSES-rust-std.html": "<html><body><h1>Copyright notices for The Rust Standard Library</h1></body></html>" });
    render(<Licenses onClose={() => {}} base="/" notices={noticesFor("desktop")} />);
    fireEvent.click(screen.getByRole("button", { name: /THIRD_PARTY_LICENSES-rust-std\.html$/ }));
    await waitFor(() => expect(screen.getByLabelText("THIRD_PARTY_LICENSES-rust-std.html").textContent)
      .toBe("Copyright notices for The Rust Standard Library"));
    fireEvent.click(screen.getByRole("button", { name: "Licenses" }));
    fireEvent.click(screen.getByRole("button", { name: /THIRD_PARTY_LICENSES-python\.txt$/ }));
    await waitFor(() => expect(screen.getByRole("alert").textContent)
      .toBe("THIRD_PARTY_LICENSES-python.txt could not be read: the app answered 404."));
  });
});

describe("where the dialog opens from", () => {
  it("the account menu, which offers Licenses", () => {
    const onLicenses = vi.fn();
    render(<TitleBar workspace={null} pendingApprovals={0} onSearch={() => {}} onApprovals={() => {}} account="you@example.org" onLicenses={onLicenses} />);
    fireEvent.click(screen.getByRole("button", { name: "Your account, you@example.org" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Licenses" }));
    expect(onLicenses).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("the sign-in screen, before anyone signs in: where the phone app opens", async () => {
    answer({ "/app/LICENSE": "\n                                 Apache License\n                           Version 2.0, January 2004\n" });
    vi.stubEnv("BASE_URL", "/app/");
    render(<SignIn />);
    fireEvent.click(screen.getByRole("button", { name: "Licenses" }));
    expect(screen.getByRole("dialog", { name: "Licenses" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /^Proofpane Community's license/ }));
    await waitFor(() => expect(screen.getByLabelText("LICENSE").textContent).toContain("Version 2.0, January 2004"));
    vi.unstubAllEnvs();
  });
});
