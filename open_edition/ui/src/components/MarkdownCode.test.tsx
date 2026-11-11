import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import { describe, expect, it } from "vitest";
import { fenceLanguage } from "../highlight";
import { MarkdownCode } from "./MarkdownCode";

const md = (text: string) => render(<ReactMarkdown components={{ code: MarkdownCode }}>{text}</ReactMarkdown>).container;

describe("a fenced block in Markdown", () => {
  it("is coloured when it names a language the page knows, by its usual short name too", () => {
    const box = md("```py\ndef f():\n    return 2\n```\n");
    expect(box.querySelector("pre code .token.keyword")?.textContent).toBe("def");
    expect(box.querySelector("pre code")?.textContent).toBe("def f():\n    return 2");
    expect([fenceLanguage("ts"), fenceLanguage("sh"), fenceLanguage("Python"), fenceLanguage("klingon"), fenceLanguage(undefined)])
      .toEqual(["typescript", "bash", "python", null, null]);
  });

  it("is shown as it is otherwise, and inline code is left alone", () => {
    const box = md("```klingon\nQapla'\n```\n\nand `inline` too\n");
    expect(box.querySelector("pre code")?.textContent).toBe("Qapla'\n");
    expect(box.querySelector("pre code .token")).toBeNull();
    expect(box.querySelector("p code")?.textContent).toBe("inline");
  });
});
