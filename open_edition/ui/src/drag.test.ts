import { describe, expect, it } from "vitest";
import type { DragEvent as ReactDragEvent } from "react";
import { CONTROL_REFUSED, dragPath, droppedPath, hasPath, PATH_TYPE } from "./drag";

function transfer() {
  const data = new Map<string, string>();
  return {
    data,
    types: [] as string[],
    effectAllowed: "",
    setData(type: string, value: string) { data.set(type, value); if (!this.types.includes(type)) this.types.push(type); },
    getData(type: string) { return data.get(type) ?? ""; },
  };
}
const event = (dt: ReturnType<typeof transfer>) => ({ dataTransfer: dt }) as unknown as ReactDragEvent;

describe("a worktree path dragged within the page", () => {
  it("carries its session and path, and the path as plain text for anything else it is dropped on", () => {
    const dt = transfer();
    dragPath(event(dt), "0123456789ab", "src/app.py");
    expect(JSON.parse(dt.getData(PATH_TYPE))).toEqual({ session: "0123456789ab", path: "src/app.py" });
    expect(dt.getData("text/plain")).toBe("src/app.py");
    expect(dt.effectAllowed).toBe("copy");
    expect(hasPath(event(dt))).toBe(true);
  });

  it("is taken only by its own session's agent", () => {
    const dt = transfer();
    dragPath(event(dt), "0123456789ab", "docs/a b.md");
    expect(droppedPath(event(dt), "0123456789ab")).toEqual({ path: "docs/a b.md" });
    expect(droppedPath(event(dt), "ba9876543210")).toBeNull();
  });

  it("is nothing when what is dropped is something else", () => {
    for (const held of ["", "not json", JSON.stringify({ session: "0123456789ab" }), JSON.stringify({ session: "0123456789ab", path: 7 }),
                        JSON.stringify({ session: "0123456789ab", path: "" })]) {
      const dt = transfer();
      dt.setData(PATH_TYPE, held);
      expect(droppedPath(event(dt), "0123456789ab"), held).toBeNull();
    }
    const files = transfer();
    files.types.push("Files");
    expect(hasPath(event(files))).toBe(false);
  });

  it("is refused, with why, when its name holds a control character, which a terminal would act on", () => {
    for (const name of ["a\x00b", "run\rrm -rf ~", "x\x1b[201~y", "tab\there", "del\x7f", "c1\x85next", "csi\x9b31m"]) {
      const dt = transfer();
      dragPath(event(dt), "0123456789ab", `src/${name}.py`);
      expect(droppedPath(event(dt), "0123456789ab"), JSON.stringify(name)).toEqual({ refused: CONTROL_REFUSED });
    }
    const dt = transfer();
    dragPath(event(dt), "0123456789ab", "src/\u00e9t\u00e9 \u00a0and more.py");            // printable, past ASCII
    expect(droppedPath(event(dt), "0123456789ab")).toEqual({ path: "src/\u00e9t\u00e9 \u00a0and more.py" });
  });
});
