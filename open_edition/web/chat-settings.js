"use strict";
// A chat's settings, for the ADE page's form and the console (the model,
// the effort and the permission mode, as the Claude app has them). The levels and the modes are the installed Claude
// Code's own, read from it by the workspace (claude_cli.py, GET /api/ade/workspace/capabilities, so that they follow
// the claude in use); until they come, and if they cannot be read, the
// image's own claude's stand. The agent checks every value, and an empty one is Claude Code's default. Everything is
// written as text.

const CHAT_SETTINGS = (function () {
  const MODEL_NAMES = { fable: "Fable", opus: "Opus", sonnet: "Sonnet", haiku: "Haiku" };
  const EFFORT_NAMES = { low: "Low", medium: "Medium", high: "High", xhigh: "Extra high", max: "Max",
                         ultracode: "Ultracode" };
  const MODE_NAMES = { default: "Ask before acting", acceptEdits: "Accept edits", plan: "Plan first", auto: "Auto",
                       bypassPermissions: "Allow everything" };
  // what each permission mode means, beside its choice: the two that ask less say so plainly
  const NOTES = {
    "": "Claude asks you before each action that needs your permission.",
    acceptEdits: "File edits go ahead without asking; commands still ask you.",
    plan: "Claude plans first and changes nothing until you accept its plan.",
    auto: "Claude Code decides which actions are safe to run without asking, and asks you for the rest.",
    bypassPermissions: "Nothing asks you in this chat: Claude acts on its own. The workspace's gates still refuse " +
                       "what they refuse.",
  };
  const EFFORT_NOTES = { ultracode: "Extra high effort, and Claude may fan out sub-agents on its own." };
  // the floor: what the image's own claude offers (claude_cli.FLOOR_EFFORTS, FLOOR_MODES)
  let offered = { claude_version: null, model_aliases: ["fable", "opus", "sonnet", "haiku"],
                  efforts: ["low", "medium", "high", "xhigh", "max", "ultracode"],
                  permission_modes: ["default", "acceptEdits", "plan", "auto", "bypassPermissions"] };
  const made = [];      // every set of fields on the page, rebuilt when the installed claude changes

  function lists() {
    return [["model", "Model", [["", "Claude Code's default model"],
                                ...offered.model_aliases.map((m) => [m, MODEL_NAMES[m] || m])]],
            ["effort", "Effort", [["", "Default effort"], ...offered.efforts.map((e) => [e, EFFORT_NAMES[e] || e])]],
            ["permission_mode", "Permissions", [["", MODE_NAMES.default],
                                                ...offered.permission_modes.filter((m) => m !== "default")
                                                  .map((m) => [m, MODE_NAMES[m] || m])]]];
  }

  function make(tag, text, cls) {
    const e = document.createElement(tag);
    if (text !== undefined && text !== null) e.textContent = String(text);
    if (cls) e.className = cls;
    return e;
  }

  function fill(select, list) {
    select.textContent = "";
    for (const [value, name] of list) {
      const o = make("option", name);
      o.value = value;
      select.appendChild(o);
    }
  }

  // three choices with their labels and what the chosen ones mean; `read` gives all three, an empty one meaning the
  // default, `show` sets them again, and `rebuild` takes the installed claude's lists and keeps what was chosen
  function fields(chosen) {
    const box = make("div", null, "chat-settings");
    const selects = {};
    for (const [key, text, list] of lists()) {
      const label = make("label", text + " ");
      const select = make("select");
      select.name = key;
      fill(select, list);
      label.appendChild(select);
      box.appendChild(label);
      selects[key] = select;
    }
    const note = make("p", null, "small");
    box.appendChild(note);
    const say = () => {
      note.textContent = [NOTES[selects.permission_mode.value] || "", EFFORT_NOTES[selects.effort.value] || ""]
        .filter(Boolean).join(" ");
    };
    selects.permission_mode.addEventListener("change", say);
    selects.effort.addEventListener("change", say);
    function read() {
      return { model: selects.model.value, effort: selects.effort.value, permission_mode: selects.permission_mode.value };
    }
    function show(given) {
      given = given || {};
      for (const [key] of lists()) {
        const value = typeof given[key] === "string" ? given[key] : "";
        // a value no longer offered, or set some other way (a model's full name), is shown as itself
        if (![...selects[key].options].some((o) => o.value === value)) {
          const o = make("option", value);
          o.value = value;
          selects[key].appendChild(o);
        }
        selects[key].value = value;
      }
      say();
    }
    function rebuild() {
      const now = read();
      for (const [key, , list] of lists()) fill(selects[key], list);
      show(now);
    }
    show(chosen);
    const f = { box, selects, show, read, rebuild };
    made.push(f);
    return f;
  }

  // the installed claude's own lists (GET /api/ade/workspace/capabilities): every set of fields follows them; the
  // version it had before, when that changed, else null
  function use(caps) {
    const strings = (x) => Array.isArray(x) && x.length > 0 && x.every((v) => typeof v === "string" && v);
    if (!caps || !strings(caps.efforts) || !strings(caps.permission_modes) || !strings(caps.model_aliases)) return null;
    const before = offered.claude_version;
    offered = { claude_version: typeof caps.claude_version === "string" ? caps.claude_version : null,
                model_aliases: caps.model_aliases, efforts: caps.efforts, permission_modes: caps.permission_modes };
    for (const f of made) f.rebuild();
    return before && offered.claude_version && before !== offered.claude_version ? before : null;
  }

  function nameOf(names, value) { return names[value] || value; }

  // the settings in a few words, for a pane's head and the chat's log
  function words(chosen) {
    chosen = chosen || {};
    const bits = [];
    if (chosen.model) bits.push(nameOf(MODEL_NAMES, chosen.model));
    if (chosen.effort) bits.push(nameOf(EFFORT_NAMES, chosen.effort) + " effort");
    if (chosen.permission_mode) bits.push(nameOf(MODE_NAMES, chosen.permission_mode));
    return bits.length ? bits.join(" · ") : "Claude Code's defaults";
  }

  return { NOTES, EFFORT_NOTES, lists, fields, use, words, version: () => offered.claude_version };
})();
