//! Proofpane Community on a phone (the open edition's manual, "The phone app"): the ADE's own UI, built
//! into the app (`npm run build:phone` in ui/, into ../dist), driving a hosted workspace. The person names the server
//! at sign-in (ui/src/api.ts, `server()`); the app runs no agents itself. The pages are the app's own (Tauri's
//! `tauri://localhost` on iOS), which the server lets call its API and open its sockets (OPEN_EDITION_APP_ORIGINS);
//! every request still carries the person's sign-in.

use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

/// The app's own pages: `tauri://localhost` on iOS, `http://tauri.localhost` (or https) on Android.
fn is_ours(url: &Url) -> bool {
    match url.scheme() {
        "tauri" | "about" => true,
        "http" | "https" => url.host_str() == Some("tauri.localhost"),
        _ => false,
    }
}

/// Whether a link out of the app's own pages goes to the phone's browser: the server's address, where the account's
/// passkeys sign what the app cannot (ui/src/components/LinkInbox.tsx, "Open it in your browser"), and mail.
fn opens_outside(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https" | "mailto")
}

/// A link out of the app's own pages: the phone's browser opens it, and the app's window stays on its own pages.
fn open_outside(app: &AppHandle, url: &Url) {
    if opens_outside(url) {
        if let Err(e) = app.opener().open_url(url.as_str(), None::<&str>) {
            println!("{url} could not be opened in the browser: {e}");
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let builder = tauri::Builder::default().plugin(tauri_plugin_opener::init());
    // Face ID, Touch ID or the passcode before the app approves anything for the person (ui/src/presence.ts)
    #[cfg(mobile)]
    let builder = builder.plugin(tauri_plugin_biometric::init());
    builder
        .setup(|app| {
            // the window, built here rather than in tauri.conf.json so that a link out of its pages goes to the
            // phone's browser, as the desktop app's does (open_edition/desktop/src-tauri/src/lib.rs, main_window)
            let for_links = app.handle().clone();
            let for_windows = app.handle().clone();
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Proofpane Community")
                .on_navigation(move |url| {
                    if is_ours(url) {
                        true
                    } else {
                        open_outside(&for_links, url);
                        false
                    }
                })
                .on_new_window(move |url, _features| {
                    if is_ours(&url) {
                        NewWindowResponse::Allow
                    } else {
                        open_outside(&for_windows, &url);
                        NewWindowResponse::Deny
                    }
                })
                .build()?;
            Ok(())
        })
        .on_page_load(|webview, payload| {
            if let tauri::webview::PageLoadEvent::Finished = payload.event() {
                // to standard output, which the simulator's and Xcode's consoles show
                println!("{} loaded {}", webview.label(), payload.url());
            }
        })
        .run(tauri::generate_context!())
        .expect("the app could not start");
}

#[cfg(test)]
mod tests {
    use super::*;

    fn url(s: &str) -> Url {
        Url::parse(s).unwrap()
    }

    #[test]
    fn keeps_its_own_pages_and_sends_the_servers_to_the_browser() {
        assert!(is_ours(&url("tauri://localhost/index.html")));
        assert!(is_ours(&url("http://tauri.localhost/index.html")));
        assert!(is_ours(&url("https://tauri.localhost/")));
        assert!(is_ours(&url("about:blank")));
        let server = url("https://pp.example/app/#prompt=7");
        assert!(!is_ours(&server));
        assert!(opens_outside(&server));
        assert!(!is_ours(&url("https://tauri.localhost.evil.example/")));
        assert!(opens_outside(&url("mailto:o@example.org")));
        assert!(!opens_outside(&url("file:///etc/hosts")));
        assert!(!opens_outside(&url("javascript:alert(1)")));
    }

    #[test]
    fn lets_its_pages_open_web_and_mail_addresses_in_the_browser_and_nothing_else() {
        // the opener plugin's script takes a click on a link to a new window (target="_blank") to its open_url,
        // which Tauri refuses unless a capability of the window grants it for that address: one grant, in the main
        // window's own capability, for the app's own pages, and no other permission of the opener's in any file
        let files = [include_str!("../capabilities/default.json"), include_str!("../capabilities/phone.json")];
        let caps: Vec<serde_json::Value> = files.iter().map(|f| serde_json::from_str(f).unwrap()).collect();
        let opener: Vec<(usize, &serde_json::Value)> = caps.iter().enumerate()
            .flat_map(|(i, c)| c["permissions"].as_array().unwrap().iter().map(move |p| (i, p)))
            .filter(|(_, p)| p.as_str().or(p["identifier"].as_str()).unwrap_or("").starts_with("opener:"))
            .collect();
        assert_eq!(opener.len(), 1, "the opener's permissions: {opener:?}");
        let (file, grant) = opener[0];
        assert_eq!(file, 0, "the grant is not in the main window's capability");
        assert_eq!(grant["identifier"], "opener:allow-open-url");
        assert!(caps[0]["windows"].as_array().unwrap().iter().any(|w| w == "main"));
        assert!(caps[0].get("local").map_or(true, |l| l == true), "the grant reaches remote pages");
        assert!(caps[0].get("remote").is_none(), "the grant reaches remote pages");
        let mut urls: Vec<&str> = grant["allow"].as_array().unwrap().iter().map(|a| a["url"].as_str().unwrap()).collect();
        urls.sort();
        assert_eq!(urls, ["http://*", "https://*", "mailto:*"]);
        assert!(grant.get("deny").is_none());
    }
}
