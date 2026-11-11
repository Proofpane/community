// What the Files tab shows a file as: an image, Markdown rendered, or text; and where a link or an image in a Markdown
// file leads in the worktree.

const IMAGES = new Set(["png", "jpg", "jpeg", "gif", "webp", "avif", "bmp", "ico", "svg"]);
const MARKDOWN = new Set(["md", "markdown", "mdown", "mkd"]);

const ending = (path: string) => {
  const base = path.split("/").pop() || "";
  return base.includes(".") ? base.slice(base.lastIndexOf(".") + 1).toLowerCase() : "";
};

/** An image the workspace sends (its IMAGE_TYPES), by the file's name. */
export const isImage = (path: string) => IMAGES.has(ending(path));
export const isMarkdown = (path: string) => MARKDOWN.has(ending(path));
/** A PDF, which the page shows in the browser's own viewer (PdfView). */
export const isPdf = (path: string) => ending(path) === "pdf";

/** Where a link or an image in the file at `from` leads in the worktree: a path from its top, or null when it leads
 *  elsewhere (another site, a mail address, a place in the same file) or above the worktree. */
export function inWorktree(from: string, href: string): string | null {
  if (!href || href.startsWith("#") || href.startsWith("//") || /^[a-z][a-z0-9+.-]*:/i.test(href)) return null;
  const bare = href.split(/[?#]/)[0];
  if (!bare) return null;
  let decoded = bare;
  try { decoded = decodeURIComponent(bare); } catch { /* as it is */ }
  const parts = decoded.startsWith("/") ? [] : from.split("/").slice(0, -1);
  for (const part of decoded.split("/")) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (!parts.length) return null;
      parts.pop();
    } else parts.push(part);
  }
  return parts.length ? parts.join("/") : null;
}
