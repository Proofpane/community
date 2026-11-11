/** How a program ended, from its exit status: a negative one is the signal that stopped it (the workspace stopping
 *  hangs its programs up); none known, it is only not running. */
export function endedHow(code: number | null | undefined, what: string): string {
  if (code == null) return `${what} is not running.`;
  if (code < 0) return `${what} was stopped.`;
  return code ? `${what} ended with status ${code}.` : `${what} ended.`;
}
