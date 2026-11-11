// @vitest-environment node
import { join, relative } from "node:path";
import ts from "typescript";
import { beforeAll, describe, expect, it } from "vitest";

// The page's own source read by TypeScript itself, with its types: every call to WebAuthn's get, and to each of the
// page's functions around it (those that take a challenge to pass on), hands the authenticator a Challenge (passkey.ts):
// a step's, one derived from the server's, or the page's own random bytes. A Challenge is made by those three makers
// alone, and a challenge the server sent is read only to be derived. So a challenge the server picked never reaches the
// authenticator as it is, where a linked session could take what the owner signs for a step of theirs.
//
// TypeScript's types are not sound, so this holds against a mistake, not against code written to get past it: an array
// widened to unknown[] takes anything, and a write through an alias of a wider type is not checked. What it does catch,
// each with a control below that plants it and sees it found: a cast to one, or to any; an any where
// one is expected; a generic that makes its type parameter from nothing, asked for one (a typed fetch among them); a
// type guard or assertion function for one; a declaration with no body that states one; and a call to WebAuthn's get,
// or to a function around it, that is not on the list.
const UI = join(__dirname, "..");
const SRC = __dirname;
const MAKERS = ["stepChallenge", "derivedChallenge", "pageChallenge"];
const KINDS = ["derived", "page", "step"];

/** The page's own source: every .ts and .tsx under src/, but its tests and the tests' kit. */
const isPage = (file: string) => file.startsWith(SRC + "/") && /\.tsx?$/.test(file) && !/\.test\.tsx?$/.test(file) && !file.endsWith("/e2eTestkit.ts");

/** The page's source as one program, as tsconfig.json reads it; with `planted`, files of this test's own (name under
 *  src/: text) read as page files too, beside `old`'s files, which are not read or parsed again. */
function programOf(planted: Record<string, string> = {}, old?: ts.Program): ts.Program {
  const config = ts.readConfigFile(join(UI, "tsconfig.json"), ts.sys.readFile);
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, UI);
  const extra = new Map(Object.entries(planted).map(([name, text]) => [join(SRC, name), text]));
  const host = ts.createCompilerHost(parsed.options);
  const { getSourceFile, fileExists, readFile } = host;
  host.getSourceFile = (file, language, onError, fresh) => extra.has(file)
    ? ts.createSourceFile(file, extra.get(file)!, language, true)
    : old?.getSourceFile(file) ?? getSourceFile.call(host, file, language, onError, fresh);
  host.fileExists = (file) => extra.has(file) || fileExists.call(host, file);
  host.readFile = (file) => extra.get(file) ?? readFile.call(host, file);
  return ts.createProgram([...parsed.fileNames.filter(isPage), ...extra.keys()], parsed.options, host, old);
}

/** The checks, over the page files of `program`: each reads only the files `scanned` keeps (all of them, but for the
 *  controls), while the functions around WebAuthn's get are found in every page file. */
function auditOf(program: ts.Program, scanned: (file: string) => boolean = () => true) {
  const checker = program.getTypeChecker();
  const all = program.getSourceFiles().filter((sf) => isPage(sf.fileName) && !sf.isDeclarationFile);
  const files = all.filter((sf) => scanned(sf.fileName));

  function nodes<T extends ts.Node>(test: (n: ts.Node) => n is T, among = files): T[] {
    const found: T[] = [];
    const visit = (n: ts.Node) => { if (test(n)) found.push(n); ts.forEachChild(n, visit); };
    for (const sf of among) visit(sf);
    return found;
  }

  /** The function around `n`, as "file name" for a function, "Class.method" for a method, or the variable that holds
   *  it. */
  function whereOf(n: ts.Node): string {
    const file = relative(SRC, n.getSourceFile().fileName);
    for (let at: ts.Node | undefined = n.parent; at; at = at.parent) {
      if (ts.isFunctionDeclaration(at) && at.name) return `${file} ${at.name.text}`;
      if (ts.isMethodDeclaration(at) && ts.isIdentifier(at.name)) {
        const owner = ts.isClassDeclaration(at.parent) && at.parent.name ? `${at.parent.name.text}.` : "";
        return `${file} ${owner}${at.name.text}`;
      }
      if ((ts.isArrowFunction(at) || ts.isFunctionExpression(at)) && ts.isVariableDeclaration(at.parent) && ts.isIdentifier(at.parent.name)) {
        return `${file} ${at.parent.name.text}`;
      }
    }
    return file;
  }

  /** A declaration where it is: "file name". */
  const named = (n: ts.NamedDeclaration) => `${relative(SRC, n.getSourceFile().fileName)} ${n.name?.getText() ?? ""}`;

  /** The kinds of Challenge a type is ("derived", "page", "step"), or null for a type that is not a Challenge (a
   *  server's string, bytes, any). */
  function kindsOf(type: ts.Type, at: ts.Node): string[] | null {
    if (type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) return null;
    const members = type.isUnion() ? type.types : [type];
    const kinds = new Set<string>();
    for (const m of members) {
      const brand = checker.getPropertiesOfType(m).find((p) => String(p.escapedName).startsWith("__@madeAs"));
      if (!brand) return null;
      const t = checker.getTypeOfSymbolAtLocation(brand, at);
      for (const k of t.isUnion() ? t.types : [t]) {
        if (!k.isStringLiteral()) return null;
        kinds.add(k.value);
      }
    }
    return [...kinds].sort();
  }

  /** The type of `name` on a value of type `type` (an argument's challenge, its publicKey), or null. */
  function member(type: ts.Type, name: string, at: ts.Node): ts.Type | null {
    const p = checker.getPropertyOfType(type, name);
    return p ? checker.getTypeOfSymbolAtLocation(p, at) : null;
  }

  /** Whether a type the page declares (Ask, Prepared, Ceremony, an options literal), as against one of the browser's
   *  or a library's, which is not looked into. */
  const ours = (t: ts.Type) => (t.getSymbol()?.declarations ?? []).some((d) => isPage(d.getSourceFile().fileName));

  /** Whether a value of type `t` carries a Challenge: is one, or one of a union's or an intersection's types does, or
   *  one of the page's own types has one among its properties, or a reference (an array, a Promise, a Map) has one
   *  among its type arguments; four deep. */
  function carries(t: ts.Type, at: ts.Node, depth = 0): boolean {
    if (kindsOf(t, at)) return true;
    if (depth >= 4) return false;
    if (t.isUnion() || t.isIntersection()) return t.types.some((m) => carries(m, at, depth + 1));
    if (ours(t) && checker.getPropertiesOfType(t).some((p) => carries(checker.getTypeOfSymbolAtLocation(p, at), at, depth + 1))) return true;
    const reference = !!(t.flags & ts.TypeFlags.Object) && !!((t as ts.ObjectType).objectFlags & ts.ObjectFlags.Reference);
    return reference && checker.getTypeArguments(t as ts.TypeReference).some((a) => carries(a, at, depth + 1));
  }

  /** Whether a call is to `CredentialsContainer`'s `name` (WebAuthn's, however the page reached
   *  navigator.credentials). */
  function credentialsCall(call: ts.CallExpression, name: string): boolean {
    if (!ts.isPropertyAccessExpression(call.expression) || call.expression.name.text !== name) return false;
    const declaration = checker.getResolvedSignature(call)?.getDeclaration();
    const owner = declaration?.parent;
    return !!owner && ts.isInterfaceDeclaration(owner) && owner.name.text === "CredentialsContainer";
  }

  /** The page's functions around the WebAuthn get, in every page file: those with a parameter that is, or carries as
   *  its `challenge`, a Challenge. With the index of that parameter, whether it carries it (true) or is it (false), and
   *  where it is. */
  function helpers(): Map<ts.Declaration, { index: number; carries: boolean; name: string }> {
    const found = new Map<ts.Declaration, { index: number; carries: boolean; name: string }>();
    const fn = (n: ts.Node): n is ts.FunctionLikeDeclaration => ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n) || ts.isArrowFunction(n) || ts.isFunctionExpression(n);
    for (const f of nodes(fn, all)) {
      f.parameters.forEach((p, index) => {
        if (!p.type) return;                // a helper says what it takes; a callback's parameter is typed by its caller
        const t = checker.getTypeFromTypeNode(p.type);
        const carried = member(t, "challenge", p);
        const carries = !kindsOf(t, p) && !!carried && !!kindsOf(carried, p);
        if (kindsOf(t, p) || carries) found.set(f, { index, carries, name: whereOf(f.body ?? f) });
      });
    }
    return found;
  }

  /** The name a call is made by: `f(...)` or `x.f(...)`. */
  const calledName = (call: ts.CallExpression) =>
    ts.isIdentifier(call.expression) ? call.expression.text : ts.isPropertyAccessExpression(call.expression) ? call.expression.name.text : "";

  /** Each call to a function around WebAuthn's get, as "where -> function: the kinds it is handed", and the failures:
   *  a call without its challenge, or with one that is no Challenge. */
  function callers(around = helpers()) {
    const seen: string[] = [];
    const wrong: string[] = [];
    const names = new Set([...around.values()].map((h) => h.name.split(" ")[1].split(".").at(-1)!));
    for (const call of nodes(ts.isCallExpression).filter((c) => names.has(calledName(c)))) {
      const target = checker.getResolvedSignature(call)?.getDeclaration();
      const h = target && around.get(target);
      if (!h) continue;
      const arg = call.arguments[h.index];
      if (!arg) { wrong.push(`${whereOf(call)} calls ${h.name} without its challenge`); continue; }
      const t = checker.getTypeAtLocation(arg);
      const kinds = kindsOf(h.carries ? member(t, "challenge", arg) ?? t : t, arg);
      if (!kinds || kinds.some((k) => !KINDS.includes(k))) { wrong.push(`${whereOf(call)} hands ${h.name} ${arg.getText()}`); continue; }
      seen.push(`${whereOf(call)} -> ${h.name.split(" ")[1]}: ${kinds.join("|")}`);
    }
    return { seen: seen.sort(), wrong };
  }

  /** Each cast to a type that carries a Challenge: the makers' own, and no other. */
  const casts = () => nodes((n): n is ts.AsExpression | ts.TypeAssertion => ts.isAsExpression(n) || ts.isTypeAssertionExpression(n))
    .filter((n) => carries(checker.getTypeFromTypeNode(n.type), n)).map(whereOf).sort();

  /** Whether an identifier is a name, not a value: a declaration's or a property's (but a shorthand property's, which
   *  is its value too). */
  const isName = (e: ts.Node) => !ts.isShorthandPropertyAssignment(e.parent)
    && ((e.parent as ts.NamedDeclaration).name === e || (ts.isPropertyAccessExpression(e.parent) && e.parent.name === e));

  /** Each expression of type any where what is expected carries a Challenge (an initializer, an argument, a property,
   *  a return, an assignment): TypeScript lets an any stand for one with no cast. */
  const anyInto = () => nodes(ts.isExpression).filter((e) => {
    if (isName(e) || !(checker.getTypeAtLocation(e).flags & ts.TypeFlags.Any)) return false;
    const wanted = checker.getContextualType(e);
    return !!wanted && carries(wanted, e);
  }).map((e) => `${whereOf(e)}: ${e.getText()}`).sort();

  /** Whether a file imports passkey.ts. */
  const importsPasskey = (sf: ts.SourceFile) => sf.statements.some((s) => ts.isImportDeclaration(s)
    && (checker.getSymbolAtLocation(s.moduleSpecifier)?.declarations ?? []).some((d) => d.getSourceFile().fileName === join(SRC, "passkey.ts")));

  /** Each cast to any in a file that imports passkey.ts. */
  const anyCasts = () => nodes((n): n is ts.AsExpression | ts.TypeAssertion => ts.isAsExpression(n) || ts.isTypeAssertionExpression(n),
    files.filter(importsPasskey)).filter((n) => n.type.kind === ts.SyntaxKind.AnyKeyword).map(whereOf).sort();

  /** Each call to a generic that makes a type parameter from nothing (no parameter's type names it), asked for one that
   *  carries a Challenge, explicitly or by what the call's place expects: what it returns is whatever it was asked for,
   *  with no cast where it is called (a typed fetch, api<Ask>(...), among them). */
  const producers = () => nodes(ts.isCallExpression).filter((call) => {
    const signature = checker.getResolvedSignature(call);
    const declaration = signature?.getDeclaration();
    if (!signature || !declaration?.typeParameters?.length) return false;
    const params = new Set(declaration.typeParameters.map((t) => t.name.text));
    const taken = new Set<string>();
    const see = (n: ts.Node) => {
      if (ts.isTypeReferenceNode(n) && ts.isIdentifier(n.typeName) && params.has(n.typeName.text)) taken.add(n.typeName.text);
      ts.forEachChild(n, see);
    };
    for (const p of declaration.parameters) if (p.type) see(p.type);
    if ([...params].every((name) => taken.has(name))) return false;
    const returned = checker.getReturnTypeOfSignature(signature);
    return carries(checker.getAwaitedType(returned) ?? returned, call);
  }).map(whereOf).sort();

  /** Each type guard or assertion function for something that carries a Challenge (`x is Challenge`): it makes one of
   *  whatever it is handed. */
  const guards = () => nodes(ts.isTypePredicateNode)
    .filter((n) => !!n.type && carries(checker.getTypeFromTypeNode(n.type), n)).map(whereOf).sort();

  /** Each declaration with no body that states something carrying a Challenge, which no code made: an overload's
   *  signature, a declared function, a declared variable or property. */
  const bodiless = () => [
    ...nodes((n): n is ts.FunctionDeclaration | ts.MethodDeclaration => ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n))
      .filter((n) => !n.body).filter((n) => {
        const signature = checker.getSignatureFromDeclaration(n);
        return !!signature && carries(checker.getReturnTypeOfSignature(signature), n);
      }).map(named),
    ...nodes((n): n is ts.VariableDeclaration | ts.PropertyDeclaration => ts.isVariableDeclaration(n) || ts.isPropertyDeclaration(n))
      .filter((n) => !!(ts.getCombinedModifierFlags(n) & ts.ModifierFlags.Ambient) && carries(checker.getTypeAtLocation(n), n)).map(named),
  ].sort();

  return { checker, nodes, whereOf, kindsOf, member, credentialsCall, helpers, callers, casts, anyInto, anyCasts, producers, guards, bodiless };
}

let program: ts.Program;
let page: ReturnType<typeof auditOf>;

beforeAll(() => {
  program = programOf();
  page = auditOf(program);
}, 120000);

// Each way round the checks that a control plants, as a file of the page's own; the controls read these files alone.
const PLANTED: Record<string, string> = {
  // the server's bytes cast to any, in a binding typed Challenge
  "auditPlantedAny.ts": `import { api } from "./api";
import { assertionWith, type Challenge } from "./passkey";

export async function plantedAny() {
  const o = await api<{ publicKey: { nonce: string } }>("/api/auth/passkey/options", { method: "POST" });
  const c: Challenge = Uint8Array.from(atob(o.publicKey.nonce), (ch) => ch.charCodeAt(0)) as any;
  return assertionWith({ challenge: c });
}
`,
  // an any with no cast at all: parsed JSON, handed on as the challenge
  "auditPlantedJson.ts": `import { assertionWith } from "./passkey";

export async function plantedJson(text: string) {
  const c = JSON.parse(text);
  return assertionWith({ challenge: c });
}
`,
  // a generic that makes its type parameter from nothing: asked for an Ask by a type argument, and for a Challenge by
  // the type of the binding it is put in
  "auditPlantedMade.ts": `import { api } from "./api";
import { assertionWith, type Ask, type Challenge } from "./passkey";

const shaped = <T>(v: unknown): T => v as T;

export async function plantedTyped() {
  return assertionWith(await api<Ask>("/api/account/confirm/options", { method: "POST" }));
}

export async function plantedInferred(raw: unknown) {
  const c: Challenge = shaped(raw);
  return assertionWith({ challenge: c });
}
`,
  // a type guard that says the server's bytes are a Challenge
  "auditPlantedGuard.ts": `import { assertionWith, type Challenge } from "./passkey";

const isChallenge = (v: unknown): v is Challenge => v instanceof Uint8Array;

export async function plantedGuard(raw: unknown) {
  if (isChallenge(raw)) return assertionWith({ challenge: raw });
}
`,
  // a signature with no body, and a declared value, each stating a Challenge that no code made
  "auditPlantedBodiless.ts": `import { assertionWith, type Challenge } from "./passkey";

function fromServer(v: unknown): Challenge;
function fromServer(v: unknown) { return v; }

declare const planted: Challenge;

export async function plantedOverload(raw: unknown) {
  return assertionWith({ challenge: fromServer(raw) });
}

export async function plantedDeclared() {
  return assertionWith({ challenge: planted });
}
`,
  // casts to one, and to an array of them
  "auditPlantedCast.ts": `import { assertionWith, type Challenge } from "./passkey";

export async function plantedCast(raw: Uint8Array<ArrayBuffer>) {
  return assertionWith({ challenge: raw as Challenge<"derived"> });
}

export async function plantedCastList(raw: Uint8Array<ArrayBuffer>) {
  const [c] = [raw] as Challenge[];
  return assertionWith({ challenge: c });
}

// a cast to any that hands nothing on, in a file that imports passkey.ts and not api.ts: named all the same
export function plantedLoose(raw: unknown) {
  return (raw as any).length as number;
}
`,
};

// TypeScript works out the page's types as these ask for them, which takes some seconds the first time
describe("what the page hands the authenticator", { timeout: 120000 }, () => {
  it("calls WebAuthn's get in one place, assertionWith, with a Challenge and user verification required", () => {
    const { checker, nodes, whereOf, kindsOf, member, credentialsCall } = page;
    const gets = nodes(ts.isCallExpression).filter((c) => credentialsCall(c, "get"));
    expect(gets.map(whereOf)).toEqual(["passkey.ts assertionWith"]);
    const [get] = gets;
    const options = checker.getTypeAtLocation(get.arguments[0]);
    const publicKey = member(options, "publicKey", get)!;
    expect(kindsOf(member(publicKey, "challenge", get)!, get)).toEqual(KINDS);
    const literal = (get.arguments[0] as ts.ObjectLiteralExpression).properties.find((p) => p.name?.getText() === "publicKey") as ts.PropertyAssignment;
    const uv = (literal.initializer as ts.ObjectLiteralExpression).properties.find((p) => p.name?.getText() === "userVerification") as ts.PropertyAssignment;
    expect(uv && ts.isStringLiteral(uv.initializer) && uv.initializer.text).toBe("required");
  });

  it("hands each function around it a step's challenge, a derived one, or the page's own, from these callers alone", () => {
    const around = page.helpers();
    // those that hand it to the authenticator, and those that only read what it is made for
    expect([...around.values()].map((h) => h.name).sort()).toEqual(["components/Channel.tsx signingWhat",
      "e2eKeys.ts allowOf", "e2eKeys.ts prfTouch", "e2eKeys.ts sameAccount", "e2eKeys.ts signAndTouch", "e2eKeys.ts signStatement",
      "e2eKeys.ts signersOf", "passkey.ts assertion", "passkey.ts assertionWith", "passkey.ts signPrepared"]);
    const { seen, wrong } = page.callers(around);
    expect(wrong).toEqual([]);
    // the ceremonies whose challenge the server picks hand on a derived one; the steps a step's; the touches that only
    // ask a passkey's PRF the page's own. A caller not listed here fails it, whatever it hands on (a list of those
    // that must be there would let a new caller pass).
    expect(seen).toEqual([
      // to the authenticator
      "passkey.ts assertion -> assertionWith: derived|page|step",
      "e2eKeys.ts prfTouch -> assertionWith: derived|page|step",
      "e2eKeys.ts signStatement -> assertionWith: step",
      "passkey.ts signInWithPasskey -> assertion: derived",
      "passkey.ts confirmWithPasskey -> assertion: derived",
      "passkey.ts signPrepared -> assertion: step",
      "components/Channel.tsx sign -> signPrepared: step",
      "components/Channel.tsx sign -> signPrepared: step",
      // a version of a task's grant, signed over its step challenge as a command is
      "components/TaskGrant.tsx sign -> signPrepared: step",
      // the Manager's holder and each goal to a linked Manager, signed over their step challenges
      "components/ControllerPanel.tsx sign -> signPrepared: step",
      "components/ControllerPanel.tsx signAndSend -> signPrepared: step",
      "e2eKeys.ts CancelChange.withPasskey -> assertionWith: derived",
      "e2eKeys.ts Enrol.firstTouch -> prfTouch: page",
      "e2eKeys.ts Enrol.secondTouch -> prfTouch: derived",       // the passkey added, its own confirmation
      "e2eKeys.ts Enrol.confirmAndSend -> assertionWith: derived",
      "e2eKeys.ts RenewAnchors.touch -> prfTouch: derived",
      "e2eKeys.ts TurnOn.firstTouch -> prfTouch: step",
      "e2eKeys.ts TurnOn.secondTouch -> prfTouch: page",
      "e2eKeys.ts TurnOnAgain.signAndSend -> signStatement: step",
      // the upgrade: its statement signed over its step challenge, each touch over its derived one
      "e2eKeys.ts Upgrade.prepare -> sameAccount: step",
      "e2eKeys.ts Upgrade.signAndSend -> prfTouch: step",
      "e2eKeys.ts Upgrade.touch -> prfTouch: derived",
      "e2eKeys.ts Revoke.sign -> signStatement: step",          // signAndSend and signAndSendWithCode share sign()
      "e2eKeys.ts Rotate.signAndSend -> signStatement: step",
      "e2eKeys.ts Rotate.signAndSendWithCode -> signStatement: step",
      "e2eKeys.ts TurnOff.signAndSend -> signStatement: step",
      "e2eKeys.ts TurnOff.signAndSendWithCode -> signStatement: step",
      "e2eKeys.ts unlockWithTouch -> prfTouch: page",
      "e2e.ts unlock -> prfTouch: page",
      // the passkey check: a touch that only asks the PRF for a test input, nothing sent or signed
      "e2eKeys.ts passkeyCheck -> prfTouch: page",
      // one press on Approve: a prepared answer's step, signed by the touch that unlocks too, or alone
      "e2eKeys.ts signAndTouch -> assertionWith: step",
      "components/LinkInbox.tsx press -> signAndTouch: step",
      "components/LinkInbox.tsx press -> signPrepared: step",
      // to the functions that only read what a step is
      "components/Channel.tsx MoveToWorkspace -> signingWhat: step",
      "components/Channel.tsx SignBox -> signingWhat: step",
      "components/Channel.tsx review -> signingWhat: step",
      "components/Channel.tsx review -> signingWhat: step",
      "e2eKeys.ts newMaster -> sameAccount: step",
      "e2eKeys.ts TurnOn.prepare -> sameAccount: step",
      "e2eKeys.ts TurnOnAgain.prepare -> sameAccount: step",
      "e2eKeys.ts Rotate.prepare -> sameAccount: step",
      "e2eKeys.ts TurnOff.prepare -> sameAccount: step",
      "e2eKeys.ts Revoke.prepare -> signersOf: step",
      "e2eKeys.ts Revoke.sign -> signersOf: step",
      "e2eKeys.ts Rotate.prepare -> signersOf: step",
      "e2eKeys.ts Rotate.signAndSend -> signersOf: step",
      "e2eKeys.ts Rotate.signAndSendWithCode -> signersOf: step",
      "e2eKeys.ts TurnOff.prepare -> signersOf: step",
      "e2eKeys.ts TurnOff.signAndSend -> signersOf: step",
      "e2eKeys.ts TurnOff.signAndSendWithCode -> signersOf: step",
      "e2eKeys.ts signersOf -> allowOf: step",
    ].sort());
  });

  it("reads a challenge the server sent only to derive one, but adding a passkey's (webauthn.create, no step's)", () => {
    const { checker, nodes, whereOf, kindsOf, credentialsCall } = page;
    const named = (n: ts.Node): n is ts.PropertyAccessExpression | ts.ElementAccessExpression | ts.BindingElement =>
      (ts.isPropertyAccessExpression(n) && n.name.text === "challenge")
      || (ts.isElementAccessExpression(n) && ts.isStringLiteralLike(n.argumentExpression) && n.argumentExpression.text === "challenge")
      || (ts.isBindingElement(n) && (n.propertyName ?? n.name).getText() === "challenge");
    const reads = nodes(named).filter((n) => !kindsOf(checker.getTypeAtLocation(n), n));
    const where = reads.map((n) => {
      const call = n.parent;
      const derived = ts.isCallExpression(call) && call.arguments.includes(n as ts.Expression) && ts.isIdentifier(call.expression) && call.expression.text === "derivedChallenge";
      return `${whereOf(n)}${derived ? " -> derivedChallenge" : ""}`;
    });
    expect(where.sort()).toEqual(["passkey.ts ceremony -> derivedChallenge", "passkey.ts makePasskey"]);
    const creates = nodes(ts.isCallExpression).filter((c) => credentialsCall(c, "create"));
    expect(creates.map(whereOf)).toEqual(["passkey.ts makePasskey"]);
  });

  it("makes a Challenge only in the three makers: no other cast to one, nor any standing for one, nor a guard", () => {
    // what carries one: a Challenge, or a type the page declares (Ask, Prepared, Ceremony, an options literal), an
    // array, a Promise, with one among its properties or type arguments, four deep
    expect(page.casts()).toEqual(MAKERS.map((m) => `passkey.ts ${m}`).sort());
    expect(page.anyInto()).toEqual([]);
    expect(page.anyCasts()).toEqual([]);
    expect(page.producers()).toEqual([]);
    expect(page.guards()).toEqual([]);
    expect(page.bodiless()).toEqual([]);
    // and the brand is named nowhere else but its declaration
    const brand = page.nodes(ts.isIdentifier).filter((i) => i.text === "madeAs");
    expect(brand.map((i) => relative(SRC, i.getSourceFile().fileName))).toEqual(["passkey.ts", "passkey.ts"]);
  });

  it("finds each way round them that it knows, planted in the page", () => {
    const planted = auditOf(programOf(PLANTED, program), (file) => file.startsWith(join(SRC, "auditPlanted")));
    expect(planted.casts()).toEqual(["auditPlantedCast.ts plantedCast", "auditPlantedCast.ts plantedCastList"]);
    expect(planted.anyInto()).toEqual([
      "auditPlantedAny.ts plantedAny: Uint8Array.from(atob(o.publicKey.nonce), (ch) => ch.charCodeAt(0)) as any",
      "auditPlantedJson.ts plantedJson: c",
    ]);
    expect(planted.anyCasts()).toEqual(["auditPlantedAny.ts plantedAny", "auditPlantedCast.ts plantedLoose"]);
    expect(planted.producers()).toEqual(["auditPlantedMade.ts plantedInferred", "auditPlantedMade.ts plantedTyped"]);
    expect(planted.guards()).toEqual(["auditPlantedGuard.ts isChallenge"]);
    expect(planted.bodiless()).toEqual(["auditPlantedBodiless.ts fromServer", "auditPlantedBodiless.ts planted"]);
    // and each planted call to assertionWith is one the page's list does not have, or hands it no Challenge
    const { seen, wrong } = planted.callers();
    expect(seen).toHaveLength(8);
    expect(wrong).toEqual(["auditPlantedJson.ts plantedJson hands passkey.ts assertionWith { challenge: c }"]);
  });
});
