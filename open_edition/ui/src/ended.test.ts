import { describe, expect, it } from "vitest";
import { endedHow } from "./ended";

describe("how a program ended", () => {
  it("says a status, a stop, or only that it is not running", () => {
    expect(endedHow(0, "The program")).toBe("The program ended.");
    expect(endedHow(3, "The program")).toBe("The program ended with status 3.");
    expect(endedHow(-1, "The shell")).toBe("The shell was stopped.");
    expect(endedHow(null, "The program")).toBe("The program is not running.");
    expect(endedHow(undefined, "The program")).toBe("The program is not running.");
  });
});
