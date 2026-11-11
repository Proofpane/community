# xterm.js, vendored

The workspace's terminal page (`../../terminal.html`) draws a session's terminal with xterm.js. The pages load
nothing from another site, so the library is kept here, copied unchanged from the npm packages below. MIT licence:
`LICENSE` (xterm.js) and `LICENSE-addon-fit` (the fit addon), each as its package ships it.

| File | From | sha256 of the file |
|---|---|---|
| `xterm.js` | `@xterm/xterm` 6.0.0, `lib/xterm.js` | `14903579ff54664cd72f8e8699e6961a6272c21863ec1c3b118cdc8af5d4a972` |
| `xterm.css` | `@xterm/xterm` 6.0.0, `css/xterm.css` | `854a7c0fb70e8b1a083c16797ab827299fb18744f5ad34f227b48337e33293c6` |
| `addon-fit.js` | `@xterm/addon-fit` 0.11.0, `lib/addon-fit.js` | `ba3ea256ce0620a0992a197d6c9baea64823fc93d8da07a9e366ca9943c18527` |
| `LICENSE` | `@xterm/xterm` 6.0.0 | `b569f629d00f2626a8100df2a1798210535621e42164dfd426a6fe5aac7b0ccd` |
| `LICENSE-addon-fit` | `@xterm/addon-fit` 0.11.0 | `e256f01188af527e4d06d21d06fbf785ae9c50d4b328bf03cbe0ba7f0aa4228f` |

The package tarballs these came from, as `npm pack` fetched them on 2026-09-28:
`xterm-xterm-6.0.0.tgz` sha256 `908e66e04af6c8dc6b00dd3b54de088e2e81e5ed866284fd6c2fb3c2d1c7a3f6`,
`xterm-addon-fit-0.11.0.tgz` sha256 `26003b4517a132b64e4ff228fd88a5fda3fff5e606c76093f6dcff772e9ecec0`.
`tests/test_workspace_page.py` checks each file against the table, so a file changed here fails the tests until the
table is changed with it.

xterm.js writes its theme, its cell sizes and its scrollbar into `<style>` elements it creates. The site's policy
allows no inline style, so the terminal page alone is served with `style-src 'self' 'unsafe-inline'` (the Caddyfile
says so beside it); every other page keeps `style-src 'self'`, and no page allows an inline script. The source maps
the packages ship are not kept, so a browser's developer tools may report `xterm.js.map` as missing.
