import { visible } from "../textRules";

/** What a connected app calls itself, as the page draws it: each character that is not shown as itself written as
 *  [U+XXXX] (visible), in a box that clips whatever is drawn outside its own line. The server holds a name to the
 *  rules an app's words are held to (oauth.client_name); this keeps a name it took under an older
 *  rule from being drawn over the warnings and the words beside it. */
export function AppName({ name, className = "" }: { name: string; className?: string }) {
  return <bdi className={`inline-block max-w-full overflow-hidden whitespace-pre-wrap break-words align-bottom ${className}`}>{visible(name)}</bdi>;
}
