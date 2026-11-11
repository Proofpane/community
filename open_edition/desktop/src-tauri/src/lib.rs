//! Proofpane Community's desktop shell (the open edition's manual, "The desktop app").
//!
//! It starts the open edition's desktop server (`python -m proofpane_open.desktop`) as its child and waits for
//! the one line the server prints once it listens: where (`http://127.0.0.1:PORT`) and a sign-in for its one account.
//! The window shows the ADE built into the app (its pages, `build/ui`), which asks for that line with the app's one
//! command, `local_server`, and talks to this Mac's server as it talks to a hosted one (the server allows the app's
//! origin); it also shows hosted workspaces the person signs in to (the UI's places). The answer waits for the server,
//! up to the time it is given to start, and says why if it did not.
//!
//! On quit it closes the server's standard input, which stops the server and the workspace with it (the agents'
//! terminals are hung up), and kills the server only if it has not stopped in time. If the server stops by itself,
//! the window says so.
//!
//! The window keeps to the app's own pages: a link anywhere else (a pull request on GitHub, a sign-in page) opens in
//! the person's browser. Files dropped on the window go to the page, which sends a file dropped on a terminal to the
//! worktree.
//!
//! Design mode (`open_preview`, `design_pick`): a session's preview window shows the app being built, served on this
//! Mac (`http://localhost:PORT`), with a picker in it (`picker.js`). A pick (where the page is, a CSS path to the element,
//! its HTML, text, box and styles) is shown in the main window, where the person says what to change and sends it to the
//! session's agent. The preview window keeps to this Mac's own servers, and its pages may call `design_pick` and nothing
//! else of the app's (capabilities/preview.json); the session a pick is for is the window's own, not the page's word.
//!
//! Pop-outs (`open_popout`): the control view's map, one session's map or one
//! agent in a window of its own, as a session's preview is. Its page is the app's own (`index.html?pop=...`), signed in
//! as the main window is, which keeps itself current; it keeps to the app's own pages, and of the app's own commands
//! may ask for this Mac's server and read a hosted sign-in from the keychain and call no other
//! (capabilities/popout.json). Its title says what it draws in fixed words, never an agent's name.
//!
//! What needs the person (an agent that waits, a chat's question, an approval) the server prints as it happens
//! (`{"notify": {...}}`), and the shell shows it as the system's own notification when the window is not
//! in front; it says the tool an agent would use, never its command.
//!
//! Which Python and which open edition: `PROOFPANE_DESKTOP_PYTHON` and `PROOFPANE_DESKTOP_ROOT` when both are set (a
//! developer's checkout), else the ones the app carries in its resources (`python/` and `app/open_edition/`;
//! `app/oss/` beside it). The server runs with Python's -E, -s and -B: no PYTHON* variable and no package of the
//! user's reaches it, and it writes no bytecode into the app, whose files are all compiled when it is built.
//! The server's data is the app's data folder (`PROOFPANE_DESKTOP_DATA` names another, for a test or a second
//! instance); what the server writes besides its data goes to `desktop-server.log` there.

mod menu_bar;

use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Emitter, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

const START_TIMEOUT: Duration = Duration::from_secs(90);
const PREVIEW: &str = "preview-";
const POP: &str = "pop-";
/// A picked element's picture: at most this many points wide, and this many bytes as PNG.
const SHOT_MOST_POINTS: f64 = 1200.0;
const SHOT_MOST_BYTES: usize = 4 * 1024 * 1024;
const PICKER: &str = include_str!("picker.js");
const STOP_TIMEOUT: Duration = Duration::from_secs(25);
const LOG_NAME: &str = "desktop-server.log";

struct Server {
    child: Mutex<Option<Child>>,
    quitting: Arc<AtomicBool>,
}

/// This Mac's server as it said where it listens (`server`, `token`), or why it did not start, once it is known.
struct Local {
    said: Mutex<Option<Result<serde_json::Value, String>>>,
    known: Condvar,
}

fn tell(app: &AppHandle, said: Result<serde_json::Value, String>) {
    let local = app.state::<Local>();
    if let Ok(mut guard) = local.said.lock() {
        *guard = Some(said);
    }
    local.known.notify_all();
}

/// The page's one command: this Mac's server and its sign-in, once the server listens, or why it did not start.
#[tauri::command]
async fn local_server(app: AppHandle) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let local = app.state::<Local>();
        let guard = local.said.lock().map_err(|_| "The app could not read its server's state.".to_string())?;
        let (guard, _) = local
            .known
            .wait_timeout_while(guard, START_TIMEOUT + Duration::from_secs(10), |said| said.is_none())
            .map_err(|_| "The app could not read its server's state.".to_string())?;
        guard.clone().unwrap_or_else(|| Err("This Mac's workspace did not answer in time.".into()))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// The first line's `server` and `token`, if they are what they must be: an origin on this machine's loopback, and a
/// sign-in of the characters a token has.
fn local_of(said: &serde_json::Value) -> Option<serde_json::Value> {
    let server = Url::parse(said.get("server")?.as_str()?).ok()?;
    let token = said.get("token")?.as_str()?;
    let bare = server.path() == "/" && server.query().is_none() && server.fragment().is_none();
    if server.scheme() != "http" || server.host_str() != Some("127.0.0.1") || server.port().is_none() || !bare {
        return None;
    }
    if token.is_empty() || token.len() > 4096 || !token.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)) {
        return None;
    }
    Some(serde_json::json!({ "server": server.origin().ascii_serialization(), "token": token }))
}

/// The Python to run the server with and the open edition's folder (the one holding `proofpane_open/`).
fn locate(app: &AppHandle) -> Result<(PathBuf, PathBuf), String> {
    if let (Ok(python), Ok(root)) = (std::env::var("PROOFPANE_DESKTOP_PYTHON"), std::env::var("PROOFPANE_DESKTOP_ROOT")) {
        return Ok((PathBuf::from(python), PathBuf::from(root)));
    }
    let resources = app
        .path()
        .resource_dir()
        .map_err(|e| format!("The app's resources could not be found: {e}"))?;
    let python = resources.join("python").join("bin").join("python3");
    let root = resources.join("app").join("open_edition");
    if python.is_file() && root.join("proofpane_open").is_dir() {
        Ok((python, root))
    } else {
        Err(format!(
            "This build carries no Python and open edition in {}. A developer's build runs with \
             PROOFPANE_DESKTOP_PYTHON and PROOFPANE_DESKTOP_ROOT set.",
            resources.display()
        ))
    }
}

fn log_file(data: &PathBuf) -> Option<File> {
    OpenOptions::new().create(true).append(true).open(data.join(LOG_NAME)).ok()
}

/// The app's data folder, where this Mac's server keeps its workspace (PROOFPANE_DESKTOP_DATA in a developer's build).
fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    match std::env::var("PROOFPANE_DESKTOP_DATA") {
        Ok(d) if !d.is_empty() => Ok(PathBuf::from(d)),
        _ => app.path().app_data_dir().map_err(|e| format!("The app's data folder could not be found: {e}")),
    }
}

/// Start the server and wait for its line; Ok(where it listens and its sign-in) or Err(what to show).
fn start(app: &AppHandle) -> Result<(Child, serde_json::Value), String> {
    let data = data_dir(app)?;
    std::fs::create_dir_all(&data).map_err(|e| format!("The app's data folder could not be made: {e}"))?;
    let (python, root) = locate(app)?;
    let stderr = match log_file(&data) {
        Some(f) => Stdio::from(f),
        None => Stdio::null(),
    };
    let mut child = Command::new(&python)
        .args(["-E", "-s", "-B", "-m", "proofpane_open.desktop", "--data"])
        .arg(&data)
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(stderr)
        .spawn()
        .map_err(|e| format!("{} could not be started: {e}", python.display()))?;

    // the first line names the address; each after it is something to notify, and anything else goes to the log, so
    // the pipe never fills
    let stdout = child.stdout.take().ok_or("The server has no output to read.")?;
    let (tx, rx) = mpsc::channel();
    let log = log_file(&data);
    let for_notices = app.clone();
    thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let got = reader.read_line(&mut line).map(|_| line);
        let _ = tx.send(got);
        let mut log = log;
        let mut line = String::new();
        loop {
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    if !notify(&for_notices, line.trim()) {
                        if let Some(f) = log.as_mut() {
                            let _ = f.write_all(line.as_bytes());
                        }
                    }
                }
            }
        }
    });
    let failed = |child: &mut Child, why: String| {
        let _ = child.kill();
        let _ = child.wait();
        Err(format!("{why} Its log is {} in {}.", LOG_NAME, data.display()))
    };
    let line = match rx.recv_timeout(START_TIMEOUT) {
        Ok(Ok(line)) if !line.trim().is_empty() => line,
        Ok(_) => return failed(&mut child, "The server stopped before it said where it listens.".into()),
        Err(_) => return failed(&mut child, format!("The server did not start in {} seconds.", START_TIMEOUT.as_secs())),
    };
    let said: serde_json::Value = match serde_json::from_str(line.trim()) {
        Ok(v) => v,
        Err(_) => return failed(&mut child, "The server's first line was not the one expected.".into()),
    };
    match local_of(&said) {
        Some(local) => Ok((child, local)),
        None => failed(&mut child, "The server named an address that is not this machine's loopback, or no sign-in.".into()),
    }
}

/// The title and body of one of the server's `{"notify": {"title", "body"}}` lines, each cut to what a notification
/// shows: Some(None) for a notice with no title, None for a line that is not a notice.
fn notice_of(line: &str) -> Option<Option<(String, String)>> {
    let said: serde_json::Value = serde_json::from_str(line).ok()?;
    let notice = said.get("notify")?;
    let text = |key: &str, most: usize| -> String {
        notice.get(key).and_then(|v| v.as_str()).unwrap_or("").chars().take(most).collect()
    };
    let (title, body) = (text("title", 120), text("body", 240));
    Some(if title.is_empty() { None } else { Some((title, body)) })
}

/// Show one of the server's notices as the system's notification, unless the window is in front, where the page shows
/// it already. False when the line is not a notice.
fn notify(app: &AppHandle, line: &str) -> bool {
    let (title, body) = match notice_of(line) {
        None => return false,
        Some(None) => return true,
        Some(Some(both)) => both,
    };
    let in_front = app.get_webview_window("main").and_then(|w| w.is_focused().ok()).unwrap_or(false);
    if !in_front {
        // a title names an agent and a repository, never a command
        match app.notification().builder().title(&title).body(body).show() {
            Ok(()) => log::info!("notified: {title}"),
            Err(e) => log::warn!("a notification could not be shown: {e}"),
        }
    }
    true
}

fn show_failure(window: &WebviewWindow, why: &str) {
    let text = serde_json::to_string(why).unwrap_or_else(|_| "\"\"".into());
    // the app's pages have ppFailed, which says it where it fits; anything else is replaced with the message
    let _ = window.eval(&format!(
        "if (window.ppFailed) {{ window.ppFailed({text}); }} else {{ document.body.innerHTML = ''; \
         const p = document.createElement('p'); p.style.cssText = 'font: 14px -apple-system, sans-serif; margin: 40px'; \
         p.textContent = {text}; document.body.appendChild(p); }}"
    ));
}

/// Stop the server: close its input, which it takes as the shell having ended, then wait; kill it only if it has not
/// stopped in time.
fn stop(server: &Server) {
    server.quitting.store(true, Ordering::SeqCst);
    let taken = server.child.lock().ok().and_then(|mut c| c.take());
    if let Some(mut child) = taken {
        drop(child.stdin.take());
        let deadline = Instant::now() + STOP_TIMEOUT;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        log::warn!("the server did not stop in {} seconds; killing it", STOP_TIMEOUT.as_secs());
        let _ = child.kill();
        let _ = child.wait();
    }
}

/// Say so in the window if the server stops while the app is open.
fn watch(app: AppHandle) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(1));
        let state = app.state::<Server>();
        if state.quitting.load(Ordering::SeqCst) {
            return;
        }
        let ended = match state.child.lock() {
            Ok(mut guard) => match guard.as_mut() {
                Some(child) => matches!(child.try_wait(), Ok(Some(_))),
                None => return,
            },
            Err(_) => return,
        };
        if ended {
            if let Some(window) = app.get_webview_window("main") {
                show_failure(&window, "The workspace's server stopped. Quit and open Proofpane Community again.");
            }
            return;
        }
    });
}

/// A page of this Mac's own server, which a preview shows: http or https on localhost or 127.0.0.1, any port, no user
/// in it (the same as capabilities/preview.json, whose patterns cannot name [::1]).
fn is_local_page(url: &Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && matches!(url.host_str(), Some("localhost") | Some("127.0.0.1"))
        && url.username().is_empty()
        && url.password().is_none()
}

/// A session's preview window's label: a session's id is twelve lower-case hex digits.
fn preview_label(session: &str) -> Option<String> {
    let ok = session.len() == 12 && session.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c));
    ok.then(|| format!("{PREVIEW}{session}"))
}

/// Open a session's preview window at `url`, a page of this Mac's own server, or bring it forward there.
#[tauri::command]
fn open_preview(app: AppHandle, session: String, url: String) -> Result<(), String> {
    let label = preview_label(&session).ok_or("Not a session.")?;
    let url = Url::parse(url.trim()).ok().filter(is_local_page)
        .ok_or("A preview shows a server on this Mac, such as http://localhost:3000.")?;
    if let Some(window) = app.get_webview_window(&label) {
        window.navigate(url).map_err(|e| e.to_string())?;
        let _ = window.show();
        let _ = window.set_focus();
        return Ok(());
    }
    let title = format!("Preview, {}", url.port().map(|p| format!("{}:{p}", url.host_str().unwrap_or(""))).unwrap_or_else(|| url.host_str().unwrap_or("").to_string()));
    let (for_links, for_windows) = (app.clone(), app.clone());
    WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
        .title(title)
        .inner_size(1200.0, 820.0)
        .min_inner_size(480.0, 360.0)
        .initialization_script(PICKER)
        .on_navigation(move |u| {
            if is_local_page(u) {
                true
            } else {
                open_outside(&for_links, u);
                false
            }
        })
        .on_new_window(move |u, _features| {
            open_outside(&for_windows, &u);
            NewWindowResponse::Deny
        })
        .build()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// A pop-out's window label, the app's page it shows and the words of its title, from what the page asked for: the map,
/// one session's map, or one agent (the page's popOut.ts says the same); None for anything else. A session's id is
/// twelve lower-case hex digits, an agent's one to 64 letters, digits, `_` or `-`, which a label may hold.
fn popout_of(view: &str, session: Option<&str>, agent: Option<&str>) -> Option<(String, String, &'static str)> {
    let is_session = |s: &str| s.len() == 12 && s.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c));
    let is_agent = |a: &str| (1..=64).contains(&a.len()) && a.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');
    match (view, session, agent) {
        ("map", None, None) => Some((format!("{POP}map"), "index.html?pop=map".into(), "The map")),
        ("session", Some(s), None) if is_session(s) => {
            Some((format!("{POP}session-{s}"), format!("index.html?pop=session&session={s}"), "A session's map"))
        }
        ("agent", Some(s), Some(a)) if is_session(s) && is_agent(a) => {
            Some((format!("{POP}agent-{s}-{a}"), format!("index.html?pop=agent&session={s}&agent={a}"), "An agent"))
        }
        _ => None,
    }
}

/// What `open_popout` does with a pop-out's window that is open already (a test stands in for the window).
trait PopoutWindow {
    fn reload(&self) -> tauri::Result<()>;
    fn show(&self) -> tauri::Result<()>;
    fn set_focus(&self) -> tauri::Result<()>;
}

impl<R: tauri::Runtime> PopoutWindow for tauri::WebviewWindow<R> {
    fn reload(&self) -> tauri::Result<()> {
        tauri::WebviewWindow::reload(self)
    }
    fn show(&self) -> tauri::Result<()> {
        tauri::WebviewWindow::show(self)
    }
    fn set_focus(&self) -> tauri::Result<()> {
        tauri::WebviewWindow::set_focus(self)
    }
}

/// A pop-out's window: the one open already, whose page loads again, and so reads its sign-in afresh as it does when
/// it is first opened rather than showing where it stopped, and which then comes forward; else one `build` makes.
fn open_or_again<W: PopoutWindow>(open: Option<W>, build: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
    if let Some(window) = open {
        let _ = window.reload();
        let _ = window.show();
        let _ = window.set_focus();
        return Ok(());
    }
    build()
}

/// Open a pop-out of the control view's map in a window of its own, or bring it forward: the app's own page, which asks
/// the app for this Mac's server as the main window does, keeping to the app's own pages; a link elsewhere opens in the
/// person's browser.
#[tauri::command]
fn open_popout(app: AppHandle, view: String, session: Option<String>, agent: Option<String>) -> Result<(), String> {
    let (label, page, what) = popout_of(&view, session.as_deref(), agent.as_deref()).ok_or("Not a part of the map.")?;
    open_or_again(app.get_webview_window(&label), || {
        let (for_links, for_windows) = (app.clone(), app.clone());
        WebviewWindowBuilder::new(&app, &label, WebviewUrl::App(page.into()))
            .title(format!("{what} · Proofpane Community"))
            .background_color(tauri::window::Color(3, 7, 18, 255))
            .inner_size(720.0, 820.0)
            .min_inner_size(360.0, 320.0)
            .on_navigation(move |u| {
                if is_ours(u) {
                    true
                } else {
                    open_outside(&for_links, u);
                    false
                }
            })
            .on_new_window(move |u, _features| {
                open_outside(&for_windows, &u);
                NewWindowResponse::Deny
            })
            .build()
            .map(|_| ())
            .map_err(|e| e.to_string())
    })
}

/// A pick, held to what the main window shows and an agent needs, each part cut to its length; None if it is not one.
fn clean_pick(pick: &serde_json::Value) -> Option<serde_json::Value> {
    let text = |key: &str, most: usize| -> String {
        pick.get(key).and_then(|v| v.as_str()).unwrap_or("").chars().take(most).collect()
    };
    let number = |v: Option<&serde_json::Value>| v.and_then(|n| n.as_f64()).filter(|n| n.is_finite()).map(|n| n.round() as i64);
    let pair = |key: &str, a: &str, b: &str| -> Option<serde_json::Value> {
        let o = pick.get(key)?;
        Some(serde_json::json!({ a: number(o.get(a))?, b: number(o.get(b))? }))
    };
    let selector = text("selector", 600);
    if selector.is_empty() {
        return None;
    }
    let mut styles = serde_json::Map::new();
    if let Some(given) = pick.get("styles").and_then(|s| s.as_object()) {
        for (k, v) in given.iter().take(40) {
            let key_ok = !k.is_empty() && k.len() <= 40 && k.chars().all(|c| c.is_ascii_lowercase() || c == '-');
            if let (true, Some(v)) = (key_ok, v.as_str()) {
                styles.insert(k.clone(), serde_json::Value::String(v.chars().take(200).collect()));
            }
        }
    }
    let rect = pick.get("rect")?;
    Some(serde_json::json!({
        "url": text("url", 2000),
        "title": text("title", 200),
        "selector": selector,
        "html": text("html", 6100),
        "text": text("text", 600),
        "rect": { "x": number(rect.get("x"))?, "y": number(rect.get("y"))?, "width": number(rect.get("width"))?,
                  "height": number(rect.get("height"))? },
        "viewport": pair("viewport", "width", "height")?,
        "styles": styles,
    }))
}

/// A picture of a picked element: the preview's own view, snapshotted over the element's box as the page measured it
/// (a CSS pixel is a point of the view; WKWebView's coordinates start at its top left, as the page's do), clipped to
/// what is on screen, as PNG. None when there is nothing on screen to take, or the system gave no picture in time.
#[cfg(target_os = "macos")]
async fn element_shot(window: &WebviewWindow, pick: &serde_json::Value) -> Option<Vec<u8>> {
    use objc2_app_kit::{NSBitmapImageFileType, NSBitmapImageRep, NSImage};
    use objc2_core_foundation::{CGPoint, CGRect, CGSize};
    use objc2_foundation::{NSDictionary, NSError, NSNumber};
    use objc2_web_kit::{WKSnapshotConfiguration, WKWebView};

    let n = |a: &str, b: &str| pick.get(a).and_then(|o| o.get(b)).and_then(|v| v.as_f64());
    let (x, y, w, h) = (n("rect", "x")?, n("rect", "y")?, n("rect", "width")?, n("rect", "height")?);
    let (vw, vh) = (n("viewport", "width")?, n("viewport", "height")?);
    let (x0, y0, x1, y1) = (x.max(0.0), y.max(0.0), (x + w).min(vw), (y + h).min(vh));
    if x1 - x0 < 2.0 || y1 - y0 < 2.0 {
        return None;
    }
    let width = (x1 - x0).min(SHOT_MOST_POINTS);
    let (tx, rx) = std::sync::mpsc::channel::<Option<Vec<u8>>>();
    window
        .with_webview(move |view| unsafe {
            // Tauri runs this on the main thread, where WebKit is used
            let Some(main) = objc2::MainThreadMarker::new() else {
                let _ = tx.send(None);
                return;
            };
            let wk: &WKWebView = &*(view.inner() as *const WKWebView);
            let config = WKSnapshotConfiguration::new(main);
            config.setRect(CGRect::new(CGPoint::new(x0, y0), CGSize::new(x1 - x0, y1 - y0)));
            config.setSnapshotWidth(Some(&NSNumber::new_f64(width)));
            let done = block2::RcBlock::new(move |image: *mut NSImage, _error: *mut NSError| {
                let png = image.as_ref().and_then(|image| {
                    let tiff = image.TIFFRepresentation()?;
                    let rep = NSBitmapImageRep::imageRepWithData(&tiff)?;
                    let png = rep.representationUsingType_properties(NSBitmapImageFileType::PNG, &NSDictionary::new())?;
                    Some(png.to_vec())
                });
                let _ = tx.send(png);
            });
            wk.takeSnapshotWithConfiguration_completionHandler(Some(&config), &done);
        })
        .ok()?;
    let png = tauri::async_runtime::spawn_blocking(move || rx.recv_timeout(Duration::from_secs(5)).ok().flatten())
        .await
        .ok()
        .flatten()?;
    (png.len() <= SHOT_MOST_BYTES).then_some(png)
}

#[cfg(not(target_os = "macos"))]
async fn element_shot(_window: &WebviewWindow, _pick: &serde_json::Value) -> Option<Vec<u8>> {
    None
}

/// A repository's folder, picked by the person in the system's own dialog, for "Add a repository": its path, or none
/// when they cancel. Only a path comes back, which the page gives the workspace to check as it checks a typed one.
#[tauri::command]
async fn pick_folder(app: AppHandle) -> Result<Option<String>, String> {
    #[cfg(target_os = "macos")]
    {
        let (tx, rx) = std::sync::mpsc::channel::<Option<String>>();
        // AppKit's panels run on the main thread; the command's own thread waits for the answer
        app.run_on_main_thread(move || {
            let _ = tx.send(folder_panel());
        })
        .map_err(|e| e.to_string())?;
        tauri::async_runtime::spawn_blocking(move || rx.recv().ok().flatten()).await.map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = app;
        Err("A folder is picked in a dialog on macOS only: type its path.".into())
    }
}

#[cfg(target_os = "macos")]
fn folder_panel() -> Option<String> {
    use objc2_app_kit::{NSModalResponseOK, NSOpenPanel};
    use objc2_foundation::NSString;
    let main = objc2::MainThreadMarker::new()?;
    let panel = NSOpenPanel::openPanel(main);
    panel.setCanChooseDirectories(true);
    panel.setCanChooseFiles(false);
    panel.setAllowsMultipleSelection(false);
    panel.setPrompt(Some(&NSString::from_str("Add")));
    panel.setMessage(Some(&NSString::from_str("Choose the repository's own folder: the one that holds its .git folder.")));
    if panel.runModal() != NSModalResponseOK {
        return None;
    }
    panel.URL()?.path().map(|p| p.to_string())
}

/// The editors a session's worktree can be opened in, by their names as macOS knows the apps; nothing else is ever
/// opened (open_worktree).
const EDITORS: [&str; 5] = ["Visual Studio Code", "Cursor", "Zed", "Windsurf", "VSCodium"];

/// A session's worktree on this Mac: found under the app's own data folder by the session's id, never a path the page
/// gives, and only where it really is under that folder (a link out of it is not followed).
fn worktree_of(data: &std::path::Path, session: &str) -> Option<PathBuf> {
    if session.len() != 12 || !session.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) {
        return None;
    }
    let base = data.canonicalize().ok()?;
    let mut users = std::fs::read_dir(data.join("workspaces")).ok()?.flatten().collect::<Vec<_>>();
    users.sort_by_key(|e| e.file_name());
    users.into_iter().find_map(|user| {
        let work = user.path().join("sessions").join(session).join("work");
        let real = work.canonicalize().ok()?;
        (real.is_dir() && real.starts_with(&base)).then_some(real)
    })
}

/// Whether a worktree holds a task an editor runs as its folder opens (VS Code's and its kin's runOn folderOpen): the
/// session's programs can write one, and an editor that trusts the folder would run it, outside the gate. The file is
/// read as the editor reads it, not as it is spelled: VS Code takes the value in any case and through JSON's escapes,
/// and reads past a byte that is not UTF-8. A file with a NUL byte in it is not plain text, and is taken as one that
/// might.
///
/// The file is opened without waiting and only a plain file of a plain size is read: a pipe with no writer would hold
/// the open, on the app's main thread, and a link to a device such as /dev/zero would read without end. Anything else
/// there (a pipe, a device, a socket, a folder, a file past the limit, one that cannot be read) is refused unread, as
/// one that might run.
fn runs_on_open(work: &std::path::Path) -> bool {
    use std::io::Read;
    use std::os::unix::fs::OpenOptionsExt;
    let opened = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(work.join(".vscode").join("tasks.json"));
    let file = match opened {
        Ok(file) => file,
        // none there (nor a .vscode folder): nothing to run
        Err(e) if matches!(e.raw_os_error(), Some(libc::ENOENT) | Some(libc::ENOTDIR)) => return false,
        Err(_) => return true,
    };
    if !file.metadata().is_ok_and(|m| m.is_file()) {
        return true;
    }
    let mut bytes = Vec::new();
    if file.take(TASKS_MAX + 1).read_to_end(&mut bytes).is_err() || bytes.len() as u64 > TASKS_MAX {
        return true;
    }
    bytes.contains(&0) || as_json_reads(&String::from_utf8_lossy(&bytes)).contains("folderopen")
}

/// The most of a tasks.json read (runs_on_open): a real one is a few kilobytes.
const TASKS_MAX: u64 = 1024 * 1024;

/// Text as a JSON reader decodes its strings, in lower case, to find a word in it however it is spelled: each
/// backslash escape decoded where it stands, as VS Code's reader decodes it (one it does not know is dropped with its
/// backslash, as that reader drops it), and the rest kept as it is.
fn as_json_reads(text: &str) -> String {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut i = 0;
    while i < chars.len() {
        let c = chars[i];
        i += 1;
        if c != '\\' {
            out.push(c);
            continue;
        }
        let Some(&e) = chars.get(i) else { break };
        i += 1;
        match e {
            '"' | '\\' | '/' => out.push(e),
            'b' => out.push('\u{8}'),
            'f' => out.push('\u{c}'),
            'n' => out.push('\n'),
            'r' => out.push('\r'),
            't' => out.push('\t'),
            'u' => {
                let (mut value, mut digits) = (0, 0);
                while let Some(d) = chars.get(i).and_then(|d| d.to_digit(16)).filter(|_| digits < 4) {
                    value = value * 16 + d;
                    digits += 1;
                    i += 1;
                }
                if digits == 4 {
                    out.extend(char::from_u32(value));
                }
            }
            _ => {}
        }
    }
    out.to_lowercase()
}

/// Which of the editors is installed on this Mac, for the page to offer (none elsewhere).
#[tauri::command]
fn editors() -> Vec<String> {
    let mut places = vec![PathBuf::from("/Applications")];
    if let Some(home) = std::env::var_os("HOME") {
        places.push(PathBuf::from(home).join("Applications"));
    }
    EDITORS
        .iter()
        .filter(|name| cfg!(target_os = "macos") && places.iter().any(|p| p.join(format!("{name}.app")).is_dir()))
        .map(|name| name.to_string())
        .collect()
}

/// Show a session's worktree in the Finder, or open it in one of the editors: the person's own tools on the files the
/// agent works on. Only this Mac's sessions have one here.
#[tauri::command]
fn open_worktree(app: AppHandle, session: String, with: Option<String>) -> Result<(), String> {
    let work = worktree_of(&data_dir(&app)?, &session).ok_or("That session has no worktree on this Mac.")?;
    match with.as_deref() {
        None => app.opener().reveal_item_in_dir(&work).map_err(|e| e.to_string()),
        Some(editor) if EDITORS.contains(&editor) => {
            if runs_on_open(&work) {
                return Err("The worktree's .vscode/tasks.json may run a task when its folder opens, and the session \
                            could have written it: look at it in the Finder first."
                    .into());
            }
            app.opener().open_path(work.to_string_lossy(), Some(editor)).map_err(|e| e.to_string())
        }
        Some(_) => Err("That is not an editor this app opens a worktree in.".into()),
    }
}

/// A pick from a preview window, for the main window, with a picture of the element where the system gives one: the
/// session is the window's, never the page's word.
#[tauri::command]
async fn design_pick(app: AppHandle, webview_window: WebviewWindow, pick: serde_json::Value) -> Result<(), String> {
    use base64::Engine;
    let session = webview_window.label().strip_prefix(PREVIEW).ok_or("Only a preview window picks.")?.to_string();
    let pick = clean_pick(&pick).ok_or("Not a pick.")?;
    let shown = webview_window.url().map(|u| u.to_string()).unwrap_or_default();
    let shot = element_shot(&webview_window, &pick).await.map(|png| base64::engine::general_purpose::STANDARD.encode(png));
    // the session and where, never what the page holds
    log::info!("design pick for session {session} from {}, {}", shown.split(['?', '#']).next().unwrap_or(""),
               if shot.is_some() { "with a picture" } else { "without a picture" });
    app.emit_to("main", "design-pick", serde_json::json!({ "session": session, "page": shown, "pick": pick, "shot": shot }))
        .map_err(|e| e.to_string())?;
    if let Some(main) = app.get_webview_window("main") {
        let _ = main.show();
        let _ = main.set_focus();
    }
    Ok(())
}

const SIGN_INS: &str = "com.proofpane.community.sign-ins";

/// A hosted workspace's server, as its sign-in is kept by: an https origin (or http on this machine, a developer's).
fn hosted_origin(server: &str) -> Option<String> {
    let url = Url::parse(server).ok()?;
    let here = matches!(url.host_str(), Some("127.0.0.1") | Some("localhost"));
    let ok = (url.scheme() == "https" || (url.scheme() == "http" && here)) && url.username().is_empty()
        && url.password().is_none() && (url.path() == "/" || url.path().is_empty()) && url.query().is_none();
    ok.then(|| url.origin().ascii_serialization())
}

/// The sign-in kept for a hosted server, if any. In the system's keychain, under this app, so another program asks
/// the person first; the page holds it in memory only.
#[tauri::command]
fn token_get(server: String) -> Result<Option<String>, String> {
    let origin = hosted_origin(&server).ok_or("Not a hosted server.")?;
    let entry = keyring::Entry::new(SIGN_INS, &origin).map_err(|e| e.to_string())?;
    match entry.get_password() {
        Ok(t) => Ok(Some(t)),
        Err(keyring::Error::NoEntry) => Ok(None),
        Err(e) => Err(format!("The keychain did not give this app's sign-in: {e}")),
    }
}

/// Keep, or with none forget, the sign-in for a hosted server.
#[tauri::command]
fn token_set(server: String, token: Option<String>) -> Result<(), String> {
    let origin = hosted_origin(&server).ok_or("Not a hosted server.")?;
    let entry = keyring::Entry::new(SIGN_INS, &origin).map_err(|e| e.to_string())?;
    match token {
        Some(t) if !t.is_empty() && t.len() <= 4096 => entry.set_password(&t).map_err(|e| e.to_string()),
        Some(_) => Err("Not a sign-in.".into()),
        None => match entry.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(e) => Err(e.to_string()),
        },
    }
}

/// Where Proofpane Tray listens for this app: `community.sock` in the tray's data folder (PROOFPANE_TRAY_SOCKET names
/// another, a development tray's).
fn tray_socket() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("PROOFPANE_TRAY_SOCKET") {
        return Some(PathBuf::from(path));
    }
    if cfg!(target_os = "macos") {
        let home = std::env::var("HOME").ok()?;
        return Some(PathBuf::from(home).join("Library/Application Support/com.airgov.tray/community.sock"));
    }
    None
}

/// One request to the tray and its one answer. The tray answers this app alone, by its code signature (the tray's
/// community.rs): the ask is this process's, never the server's or an agent's, which run as the same user.
fn ask_tray_at(path: &std::path::Path, request: &serde_json::Value, wait: Duration) -> Result<serde_json::Value, String> {
    use std::io::Read;
    use std::os::unix::net::UnixStream;
    if !path.exists() {
        return Err("Proofpane Tray is not running on this Mac.".into());
    }
    let mut stream = UnixStream::connect(path).map_err(|_| "Proofpane Tray could not be reached.".to_string())?;
    stream.set_read_timeout(Some(wait)).ok();
    stream.set_write_timeout(Some(Duration::from_secs(5))).ok();
    stream.write_all(format!("{request}\n").as_bytes()).map_err(|_| "Proofpane Tray could not be reached.".to_string())?;
    let mut answer = Vec::new();
    let mut chunk = [0u8; 4096];
    while !answer.ends_with(b"\n") && answer.len() < 64 * 1024 {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => answer.extend_from_slice(&chunk[..n]),
            Err(e) if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) => {
                return Err("Proofpane Tray did not answer in time.".into())
            }
            Err(_) => return Err("Proofpane Tray could not be reached.".into()),
        }
    }
    match serde_json::from_slice::<serde_json::Value>(&answer) {
        Ok(v) if v.is_object() => Ok(v),
        _ => Err("Proofpane Tray's answer could not be read.".into()),
    }
}

/// Whether the tray runs here and is linked to `server`, and as whom: {reachable, linked, email}.
fn tray_links_at(path: &std::path::Path, server: &str) -> serde_json::Value {
    let links = match ask_tray_at(path, &serde_json::json!({ "ask": "links" }), Duration::from_secs(3)) {
        Ok(v) => v,
        Err(_) => return serde_json::json!({ "reachable": false, "linked": false, "email": null }),
    };
    let link = links.get("links").and_then(|l| l.as_array())
        .and_then(|l| l.iter().find(|x| x.get("server").and_then(|s| s.as_str()) == Some(server)));
    serde_json::json!({ "reachable": links.get("error").is_none(), "linked": link.is_some(),
                        "email": link.and_then(|l| l.get("email")).cloned().unwrap_or(serde_json::Value::Null) })
}

#[tauri::command]
async fn tray_status(server: String) -> Result<serde_json::Value, String> {
    let path = tray_socket().ok_or("Proofpane Tray takes this app's requests on macOS only.")?;
    tauri::async_runtime::spawn_blocking(move || tray_links_at(&path, &server)).await.map_err(|e| e.to_string())
}

/// Hand the tray the code of this app's own device sign-in at `server`, and wait for the person's answer there.
#[tauri::command]
async fn tray_approve(server: String, code: String) -> Result<serde_json::Value, String> {
    let ok_code = !code.is_empty() && code.len() <= 20 && code.chars().all(|c| c.is_ascii_alphanumeric() || c == '-');
    let ok_server = Url::parse(&server).map(|u| matches!(u.scheme(), "http" | "https")).unwrap_or(false);
    if !ok_code || !ok_server {
        return Err("Not a server and a device code.".into());
    }
    let path = tray_socket().ok_or("Proofpane Tray takes this app's requests on macOS only.")?;
    tauri::async_runtime::spawn_blocking(move || {
        let request = serde_json::json!({ "ask": "approve", "server": server, "code": code });
        let answer = ask_tray_at(&path, &request, Duration::from_secs(10 * 60))?;
        if let Some(why) = answer.get("error") {
            return Err(why.as_str().unwrap_or("Proofpane Tray refused it.").to_string());
        }
        Ok(serde_json::json!({ "approved": answer.get("approved") == Some(&serde_json::Value::Bool(true)) }))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// The app's own pages: `tauri://localhost` on macOS, `http://tauri.localhost` (or https) on Windows.
fn is_ours(url: &Url) -> bool {
    match url.scheme() {
        "tauri" | "about" => true,
        "http" | "https" => url.host_str() == Some("tauri.localhost"),
        _ => false,
    }
}

fn open_outside(app: &AppHandle, url: &Url) {
    if matches!(url.scheme(), "http" | "https" | "mailto") {
        if let Err(e) = app.opener().open_url(url.as_str(), None::<&str>) {
            log::warn!("{url} could not be opened in the browser: {e}");
        }
    }
}

fn main_window(app: &tauri::App) -> tauri::Result<WebviewWindow> {
    let for_links = app.handle().clone();
    let for_windows = app.handle().clone();
    WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Proofpane Community")
        // the app's own dark page (ui/src/index.css), so the window never shows white before the page draws
        .background_color(tauri::window::Color(3, 7, 18, 255))
        .inner_size(1400.0, 900.0)
        .min_inner_size(900.0, 600.0)
        // the page takes files dropped on it (a terminal sends them to the worktree), as it does in a browser
        .disable_drag_drop_handler()
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
        .build()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::init();
    let app = tauri::Builder::default()
        .manage(Server { child: Mutex::new(None), quitting: Arc::new(AtomicBool::new(false)) })
        .manage(Local { said: Mutex::new(None), known: Condvar::new() })
        .manage(menu_bar::MenuBar(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![local_server, open_preview, design_pick, tray_status, tray_approve,
                                                 token_get, token_set, menu_bar::menu_bar, pick_folder, editors,
                                                 open_worktree, open_popout])
        .on_window_event(|window, event| {
            // closing the main window keeps the app in the menu bar, when it has its icon there to come back by
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" && menu_bar::shown(window.app_handle()) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .on_page_load(|webview, payload| {
            if let tauri::webview::PageLoadEvent::Finished = payload.event() {
                // the address without its fragment, which carries the sign-in on the first load
                let mut url = payload.url().clone();
                url.set_fragment(None);
                log::info!("{} loaded {}", webview.label(), url);
            }
        })
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            main_window(app)?;
            match menu_bar::make(app) {
                Ok(icon) => {
                    if let Ok(mut held) = app.state::<menu_bar::MenuBar>().0.lock() {
                        *held = Some(icon);
                    }
                    menu_bar::keep_reading(app.handle().clone());
                }
                Err(e) => log::warn!("no menu-bar icon: {e}"),
            }
            let handle = app.handle().clone();
            thread::spawn(move || {
                let window = match handle.get_webview_window("main") {
                    Some(w) => w,
                    None => return,
                };
                match start(&handle) {
                    Ok((child, local)) => {
                        let state = handle.state::<Server>();
                        if state.quitting.load(Ordering::SeqCst) {
                            let mut child = child;
                            let _ = child.kill();
                            let _ = child.wait();
                            return;
                        }
                        if let Ok(mut guard) = state.child.lock() {
                            *guard = Some(child);
                        }
                        tell(&handle, Ok(local));
                        watch(handle.clone());
                    }
                    Err(why) => {
                        log::error!("{why}");
                        tell(&handle, Err(why.clone()));
                        show_failure(&window, &why);
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("the app could not be built");
    app.run(|handle, event| match event {
        RunEvent::Exit => stop(&handle.state::<Server>()),
        // the Dock's icon clicked: the window comes back when it was closed to the menu bar (or minimized), even while
        // a preview window is open, which macOS counts as a visible window
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => {
            if handle.get_webview_window("main").is_some_and(|w| !w.is_visible().unwrap_or(true) || w.is_minimized().unwrap_or(false)) {
                menu_bar::show_main(handle)
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::{is_ours, notice_of, runs_on_open, worktree_of};

    #[test]
    fn a_worktree_with_a_task_that_runs_as_its_folder_opens_is_told_from_one_without() {
        let work = std::env::temp_dir().join(format!("pp-tasks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&work);
        std::fs::create_dir_all(work.join(".vscode")).unwrap();
        assert!(!runs_on_open(&work));
        std::fs::write(work.join(".vscode").join("tasks.json"), r#"{"tasks": [{"label": "build"}]}"#).unwrap();
        assert!(!runs_on_open(&work));
        std::fs::write(work.join(".vscode").join("tasks.json"), r#"{"tasks": [{"runOptions": {"runOn": "folderOpen"}}]}"#).unwrap();
        assert!(runs_on_open(&work));
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn a_task_that_runs_as_its_folder_opens_is_found_however_the_file_spells_it() {
        let work = std::env::temp_dir().join(format!("pp-tasks-spelt-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&work);
        std::fs::create_dir_all(work.join(".vscode")).unwrap();
        let file = work.join(".vscode").join("tasks.json");
        let task = |run_on: &[u8]| [&br#"{"tasks": [{"runOptions": {"runOn": ""#[..], run_on, &br#""}}]}"#[..]].concat();
        // VS Code reads the value in any case and through JSON's escapes (one it does not know dropped, with its
        // backslash)
        for spelt in [&b"FOLDEROPEN"[..], br"folder\u004fpen", br"\u0066older\u004Fpen", br"folderO\xpen"] {
            std::fs::write(&file, task(spelt)).unwrap();
            assert!(runs_on_open(&work), "{}", String::from_utf8_lossy(spelt));
        }
        // and past a byte that is not UTF-8
        std::fs::write(&file, [&b"// \xff\n"[..], &task(b"folderOpen")].concat()).unwrap();
        assert!(runs_on_open(&work));
        // a NUL byte: not plain text (UTF-16 is read so)
        std::fs::write(&file, b"{\0}").unwrap();
        assert!(runs_on_open(&work));
        // an escape is decoded where it stands: \f is a form feed, \\ a backslash, and three digits after \u make an
        // escape dropped whole, none of them a way to the word
        for plain in [&b"\\folderOpen"[..], br"\\u0066olderOpen", br"folder\u04Fpen", br#"echo \"done\""#] {
            std::fs::write(&file, task(plain)).unwrap();
            assert!(!runs_on_open(&work), "{}", String::from_utf8_lossy(plain));
        }
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn what_is_not_a_plain_file_of_a_plain_size_is_refused_unread_and_never_waited_on() {
        let work = std::env::temp_dir().join(format!("pp-tasks-odd-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&work);
        std::fs::create_dir_all(work.join(".vscode")).unwrap();
        let file = work.join(".vscode").join("tasks.json");
        // on a thread with a deadline: a check that waits fails here rather than holding the run
        let within = |work: &std::path::Path| {
            let (tx, rx) = std::sync::mpsc::channel();
            let work = work.to_path_buf();
            std::thread::spawn(move || tx.send(runs_on_open(&work)).unwrap());
            rx.recv_timeout(std::time::Duration::from_secs(5)).expect("the check waited")
        };
        // a pipe no one writes to
        assert!(std::process::Command::new("mkfifo").arg(&file).status().unwrap().success());
        assert!(within(&work));
        std::fs::remove_file(&file).unwrap();
        // a link to a device that reads without end
        std::os::unix::fs::symlink("/dev/zero", &file).unwrap();
        assert!(within(&work));
        std::fs::remove_file(&file).unwrap();
        // a socket, which cannot be opened to be read
        let socket = std::os::unix::net::UnixListener::bind(&file).unwrap();
        assert!(within(&work));
        drop(socket);
        std::fs::remove_file(&file).unwrap();
        // a folder of the name, and a file past the limit (nothing in it runs, but it is not read)
        std::fs::create_dir(&file).unwrap();
        assert!(within(&work));
        std::fs::remove_dir(&file).unwrap();
        std::fs::write(&file, vec![b' '; super::TASKS_MAX as usize + 1]).unwrap();
        assert!(within(&work));
        // a link to a plain file is read as that file, as an editor reads it
        let plain = work.join("plain.json");
        std::fs::write(&plain, r#"{"tasks": [{"label": "build"}]}"#).unwrap();
        std::fs::remove_file(&file).unwrap();
        std::os::unix::fs::symlink(&plain, &file).unwrap();
        assert!(!within(&work));
        let _ = std::fs::remove_dir_all(&work);
    }

    #[test]
    fn a_worktree_is_found_by_its_session_s_id_under_the_data_folder_only() {
        let data = std::env::temp_dir().join(format!("pp-worktree-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&data);
        let work = data.join("workspaces").join("u1").join("sessions").join("0123456789ab").join("work");
        std::fs::create_dir_all(&work).unwrap();
        assert_eq!(worktree_of(&data, "0123456789ab"), Some(work.canonicalize().unwrap()));
        // an id is twelve lowercase hex digits: nothing else names a folder, a path least of all
        for bad in ["0123456789AB", "0123456789a", "../../etc/xx", "0123456789ab/..", "", "0123456789abc"] {
            assert_eq!(worktree_of(&data, bad), None, "{bad}");
        }
        assert_eq!(worktree_of(&data, "ba9876543210"), None);
        // a link out of the data folder is not followed
        let outside = std::env::temp_dir().join(format!("pp-outside-{}", std::process::id()));
        std::fs::create_dir_all(&outside).unwrap();
        let linked = data.join("workspaces").join("u1").join("sessions").join("aaaaaaaaaaaa");
        std::fs::create_dir_all(&linked).unwrap();
        #[cfg(unix)]
        std::os::unix::fs::symlink(&outside, linked.join("work")).unwrap();
        assert_eq!(worktree_of(&data, "aaaaaaaaaaaa"), None);
        let _ = std::fs::remove_dir_all(&data);
        let _ = std::fs::remove_dir_all(&outside);
    }

    #[test]
    fn a_notice_is_told_from_any_other_line_and_cut_to_what_a_notification_shows() {
        let line = r#"{"notify": {"title": "Codex waits for you, in demo", "body": "Fix it · Wants to use shell", "url": "/app/#session=abc"}}"#;
        assert_eq!(notice_of(line), Some(Some(("Codex waits for you, in demo".into(), "Fix it · Wants to use shell".into()))));
        assert_eq!(notice_of(r#"{"notify": {"body": "no title"}}"#), Some(None));
        assert_eq!(notice_of("INFO something the server logged"), None);
        assert_eq!(notice_of(r#"{"url": "http://127.0.0.1:1/app/"}"#), None);
        let long = format!(r#"{{"notify": {{"title": "{}", "body": "{}"}}}}"#, "t".repeat(500), "b".repeat(500));
        let (title, body) = notice_of(&long).unwrap().unwrap();
        assert_eq!((title.chars().count(), body.chars().count()), (120, 240));
    }
    use tauri::Url;

    fn ours(url: &str) -> bool {
        is_ours(&Url::parse(url).unwrap())
    }

    #[test]
    fn the_window_keeps_to_the_app_s_own_pages() {
        assert!(ours("tauri://localhost/index.html"));
        assert!(ours("http://tauri.localhost/index.html")); // the app's pages on Windows
        assert!(ours("https://tauri.localhost/index.html"));
        assert!(ours("about:blank"));
        // anything else goes to the browser, or nowhere: the servers the app talks to are not pages it shows
        for url in ["https://github.com/acme/app/pull/7", "http://127.0.0.1:62711/app/#signin=a.b", "http://localhost:62711/app/",
                    "http://127.0.0.2:80/", "https://proofpane-oss.fly.dev/app/", "https://tauri.localhost.evil.test/",
                    "file:///etc/passwd", "javascript:alert(1)", "http://evil.test/"] {
            assert!(!ours(url), "{url}");
        }
    }

    #[test]
    fn a_preview_shows_this_mac_s_own_servers_only() {
        use super::is_local_page;
        for url in ["http://localhost:3000/", "http://127.0.0.1:5173/settings?x=1#y", "https://localhost:8443/"] {
            assert!(is_local_page(&Url::parse(url).unwrap()), "{url}");
        }
        for url in ["http://example.com/", "http://localhost.evil.test/", "http://user:pw@localhost:3000/", "file:///etc/passwd",
                    "tauri://localhost/", "javascript:alert(1)", "http://10.0.0.2:3000/", "ws://localhost:3000/",
                    "http://[::1]:8080/"] {
            assert!(!is_local_page(&Url::parse(url).unwrap()), "{url}");
        }
    }

    #[test]
    fn it_asks_the_tray_one_line_and_reads_one_answer() {
        use super::{ask_tray_at, tray_links_at};
        use std::io::{BufRead, BufReader, Write};
        use std::time::Duration;
        use std::os::unix::net::UnixListener;
        let dir = std::env::temp_dir().join(format!("pp-shell-tray-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("t.sock");
        let _ = std::fs::remove_file(&path);
        let listener = UnixListener::bind(&path).unwrap();
        let answers = vec![
            r#"{"links": [{"server": "https://proofpane-oss.fly.dev", "email": "you@example.com"}]}"#,
            r#"{"links": []}"#,
            r#"{"error": "Only the Proofpane Community app may ask the tray."}"#,
            r#"{"approved": true}"#,
            "not json",
        ];
        let server = std::thread::spawn(move || {
            let mut asked = Vec::new();
            for answer in answers {
                let (mut conn, _) = listener.accept().unwrap();
                let mut line = String::new();
                BufReader::new(&conn).read_line(&mut line).unwrap();
                asked.push(line);
                conn.write_all(format!("{answer}\n").as_bytes()).unwrap();
            }
            asked
        });
        let hosted = "https://proofpane-oss.fly.dev";
        assert_eq!(tray_links_at(&path, hosted), serde_json::json!({ "reachable": true, "linked": true, "email": "you@example.com" }));
        assert_eq!(tray_links_at(&path, hosted), serde_json::json!({ "reachable": true, "linked": false, "email": null }));
        // a tray that refuses this program says so; it is reachable but not for this
        assert_eq!(tray_links_at(&path, hosted)["reachable"], false);
        let approve = serde_json::json!({ "ask": "approve", "server": hosted, "code": "ABCD-EFGH" });
        assert_eq!(ask_tray_at(&path, &approve, Duration::from_secs(5)).unwrap(), serde_json::json!({ "approved": true }));
        assert!(ask_tray_at(&path, &approve, Duration::from_secs(5)).unwrap_err().contains("could not be read"));
        let asked = server.join().unwrap();
        assert_eq!(asked[0].trim(), r#"{"ask":"links"}"#);
        assert_eq!(serde_json::from_str::<serde_json::Value>(asked[3].trim()).unwrap(), approve);
        // no tray, no socket
        assert!(ask_tray_at(&dir.join("none.sock"), &approve, Duration::from_secs(1)).unwrap_err().contains("not running"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn a_sign_in_is_kept_by_a_hosted_server_s_origin() {
        use super::hosted_origin;
        assert_eq!(hosted_origin("https://proofpane-oss.fly.dev").as_deref(), Some("https://proofpane-oss.fly.dev"));
        assert_eq!(hosted_origin("https://proofpane-oss.fly.dev/").as_deref(), Some("https://proofpane-oss.fly.dev"));
        assert_eq!(hosted_origin("http://127.0.0.1:8899").as_deref(), Some("http://127.0.0.1:8899"));
        for bad in ["http://proofpane-oss.fly.dev", "https://a@b.example", "https://b.example/app/", "https://b.example/?x",
                    "tauri://localhost", "not a url", ""] {
            assert_eq!(hosted_origin(bad), None, "{bad}");
        }
    }

    #[test]
    fn a_preview_window_belongs_to_one_session() {
        use super::preview_label;
        assert_eq!(preview_label("0123456789ab").as_deref(), Some("preview-0123456789ab"));
        for bad in ["", "0123456789a", "0123456789abc", "0123456789AB", "../../etc/pa", "main", "0123456789a*"] {
            assert_eq!(preview_label(bad), None, "{bad}");
        }
    }

    #[test]
    fn a_pop_out_is_one_part_of_the_map_in_a_window_named_for_it() {
        use super::popout_of;
        let s = "0123456789ab";
        assert_eq!(popout_of("map", None, None),
                   Some(("pop-map".into(), "index.html?pop=map".into(), "The map")));
        assert_eq!(popout_of("session", Some(s), None),
                   Some(("pop-session-0123456789ab".into(), "index.html?pop=session&session=0123456789ab".into(), "A session's map")));
        assert_eq!(popout_of("agent", Some(s), Some("a1B2_c-3")),
                   Some(("pop-agent-0123456789ab-a1B2_c-3".into(),
                         "index.html?pop=agent&session=0123456789ab&agent=a1B2_c-3".into(), "An agent")));
        let long = "a".repeat(64);
        assert!(popout_of("agent", Some(s), Some(&long)).is_some());
        // what is not a part of the map, or names what is not a session or an agent, opens nothing
        for bad in ["", "0123456789a", "0123456789abc", "0123456789AB", "../../etc/pa", "0123456789a*"] {
            assert_eq!(popout_of("session", Some(bad), None), None, "{bad}");
            assert_eq!(popout_of("agent", Some(bad), Some("a1")), None, "{bad}");
        }
        let too_long = "a".repeat(65);
        for bad in ["", "a b", "a&pop=map", "a/../b", "a.b", "a#b", "a?b", "\u{e9}", too_long.as_str()] {
            assert_eq!(popout_of("agent", Some(s), Some(bad)), None, "{bad}");
        }
        for (view, session, agent) in [("map", Some(s), None), ("map", None, Some("a1")), ("session", None, None),
                                       ("session", Some(s), Some("a1")), ("agent", Some(s), None), ("main", None, None),
                                       ("preview", Some(s), None), ("", None, None)] {
            assert_eq!(popout_of(view, session, agent), None, "{view} {session:?} {agent:?}");
        }
    }

    #[test]
    fn a_pop_out_open_already_loads_its_page_again_and_comes_forward_and_none_is_built() {
        use std::cell::{Cell, RefCell};
        // the window, stood in for: what is done with it, in order
        struct Seen<'a>(&'a RefCell<Vec<&'static str>>);
        impl super::PopoutWindow for Seen<'_> {
            fn reload(&self) -> tauri::Result<()> {
                self.0.borrow_mut().push("reload");
                Ok(())
            }
            fn show(&self) -> tauri::Result<()> {
                self.0.borrow_mut().push("show");
                Ok(())
            }
            fn set_focus(&self) -> tauri::Result<()> {
                self.0.borrow_mut().push("set_focus");
                Ok(())
            }
        }
        let (seen, built) = (RefCell::new(vec![]), Cell::new(false));
        assert_eq!(super::open_or_again(Some(Seen(&seen)), || { built.set(true); Ok(()) }), Ok(()));
        assert_eq!(*seen.borrow(), ["reload", "show", "set_focus"]);
        assert!(!built.get());
        // none open: one is built, and what building it says is the answer
        assert_eq!(super::open_or_again(None::<Seen>, || { built.set(true); Err("not built".into()) }), Err("not built".to_string()));
        assert!(built.get());
    }

    #[test]
    fn a_pop_out_s_page_may_ask_for_its_sign_in_and_nothing_else_of_the_app_s() {
        let read = |text: &str| -> serde_json::Value { serde_json::from_str(text).expect("a capability is JSON") };
        let commands = |cap: &serde_json::Value| -> Vec<String> {
            cap["permissions"].as_array().expect("its permissions").iter().filter_map(|p| p.as_str())
                .filter(|p| p.starts_with("allow-")).map(String::from).collect()
        };
        let popout = read(include_str!("../capabilities/popout.json"));
        assert_eq!(popout["windows"], serde_json::json!(["pop-*"]));
        assert_eq!(commands(&popout), ["allow-local-server", "allow-token-get"]);
        // nor Tauri's events, which would let its page raise the main window's own (a design pick, the menu-bar icon's)
        let all = popout["permissions"].as_array().expect("its permissions");
        assert!(!all.iter().filter_map(|p| p.as_str()).any(|p| p.starts_with("core:event")), "{all:?}");
        // the main window opens one; a preview's pages, which are a server's on this Mac, never do
        assert!(commands(&read(include_str!("../capabilities/default.json"))).contains(&"allow-open-popout".to_string()));
        assert!(!commands(&read(include_str!("../capabilities/preview.json"))).contains(&"allow-open-popout".to_string()));
        // each pop-out's window is one the capability names, and no preview's
        for (view, session, agent) in [("map", None, None), ("session", Some("0123456789ab"), None),
                                       ("agent", Some("0123456789ab"), Some("a1"))] {
            let (label, _, _) = super::popout_of(view, session, agent).expect("a part of the map");
            assert!(label.starts_with(super::POP) && !label.starts_with(super::PREVIEW), "{label}");
        }
    }

    #[test]
    fn a_pick_is_held_to_its_parts_and_their_lengths() {
        use super::clean_pick;
        let pick = serde_json::json!({
            "url": "http://localhost:3000/settings", "title": "App", "selector": "main#app > button.primary",
            "html": "x".repeat(10_000), "text": "Save", "rect": { "x": 10.4, "y": 20.6, "width": 120, "height": 40 },
            "viewport": { "width": 1280, "height": 800 },
            "styles": { "color": "rgb(255, 255, 255)", "Bad Key": "x", "padding": "8px 16px", "long": "y".repeat(500),
                        "number": 7 },
            "extra": "ignored"
        });
        let got = clean_pick(&pick).unwrap();
        assert_eq!(got["html"].as_str().unwrap().chars().count(), 6100);
        assert_eq!(got["rect"], serde_json::json!({ "x": 10, "y": 21, "width": 120, "height": 40 }));
        assert_eq!(got["styles"]["color"], "rgb(255, 255, 255)");
        assert!(got["styles"].get("Bad Key").is_none() && got["styles"].get("number").is_none());
        assert_eq!(got["styles"]["long"].as_str().unwrap().len(), 200);
        assert!(got.get("extra").is_none());
        // what is not a pick
        assert_eq!(clean_pick(&serde_json::json!({ "selector": "", "rect": {}, "viewport": {} })), None);
        assert_eq!(clean_pick(&serde_json::json!({ "selector": "a", "viewport": { "width": 1, "height": 1 } })), None);
        assert_eq!(clean_pick(&serde_json::json!("a string")), None);
    }

    #[test]
    fn the_first_line_must_name_this_machine_s_loopback_and_a_sign_in() {
        use super::local_of;
        let line = |server: &str, token: &str| serde_json::json!({ "server": server, "token": token, "port": 1 });
        assert_eq!(
            local_of(&line("http://127.0.0.1:62711", "eyJhIjoxfQ.c2ln")),
            Some(serde_json::json!({ "server": "http://127.0.0.1:62711", "token": "eyJhIjoxfQ.c2ln" }))
        );
        for server in ["https://127.0.0.1:62711", "http://localhost:62711", "http://10.0.0.2:62711", "http://127.0.0.1",
                       "http://127.0.0.1:62711/app/", "http://127.0.0.1:62711/?x=1", "not a url"] {
            assert_eq!(local_of(&line(server, "a.b")), None, "{server}");
        }
        for token in ["", "a b", "a.b\n", "<script>"] {
            assert_eq!(local_of(&line("http://127.0.0.1:1", token)), None, "{token}");
        }
        assert_eq!(local_of(&serde_json::json!({ "url": "http://127.0.0.1:1/app/#signin=a.b" })), None);
    }
}
