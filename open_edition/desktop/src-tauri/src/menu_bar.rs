//! The app's own menu-bar icon (a menu-bar icon of its own that shows
//! what needs the person and opens the main window). A plain four-pane window, unlike Proofpane Tray's, which carries a
//! dot, so the two are told apart in one menu bar.
//!
//! The page knows what needs the person in the place the window shows (an agent waiting for them, approvals asked) and
//! says so with `menu_bar`, from the main window only (capabilities/default.json); the menu lists those sessions, by
//! name and what they wait for (never a command), the count stands beside the icon, and choosing one shows the window
//! and tells the page (`menu-bar-open`), which opens it. Closing the window keeps the app in the menu bar, where Open
//! brings it back and Quit ends it; where the system gives no menu-bar icon, closing the window quits, as before.

use std::sync::Mutex;

use tauri::menu::{IsMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Emitter, Manager, Wry};

const ICON_ID: &str = "community";
const TITLE: &str = "Proofpane Community";
/// Sessions the page may list; it counts all of them.
pub const MOST: usize = 10;
const MOST_COUNT: u64 = 100_000;

/// The icon, once made; None where the system gave none.
pub struct MenuBar(pub Mutex<Option<TrayIcon>>);

#[derive(Debug, PartialEq)]
pub struct Waiting {
    pub session: String,
    pub title: String,
    pub note: String,
}

#[derive(Debug, PartialEq)]
pub struct Summary {
    pub place: String,
    pub waiting: Vec<Waiting>,
    pub approvals: u64,
    pub count: u64,
}

/// Text for a menu: one line, no control characters, at most `most` characters.
fn line_of(v: Option<&serde_json::Value>, most: usize) -> Option<String> {
    let text = v?.as_str()?;
    Some(text.chars().map(|c| if c.is_control() { ' ' } else { c }).collect::<String>().trim().chars().take(most).collect())
}

fn is_session(id: &str) -> bool {
    id.len() == 12 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// The page's summary, checked, or None when it is not one: a place's name; at most MOST sessions, each by its id, with
/// a title and what it waits for; the approvals asked; and the count of all of it, at least what is listed.
pub fn summary_of(v: &serde_json::Value) -> Option<Summary> {
    let place = line_of(v.get("place"), 60).filter(|p| !p.is_empty())?;
    let listed = v.get("waiting")?.as_array()?;
    if listed.len() > MOST {
        return None;
    }
    let mut waiting = Vec::with_capacity(listed.len());
    for w in listed {
        let session = w.get("session")?.as_str()?;
        if !is_session(session) {
            return None;
        }
        waiting.push(Waiting { session: session.into(), title: line_of(w.get("title"), 80)?, note: line_of(w.get("note"), 80)? });
    }
    let approvals = v.get("approvals")?.as_u64().filter(|n| *n <= MOST_COUNT)?;
    let count = v.get("count")?.as_u64().filter(|n| *n <= MOST_COUNT && *n >= waiting.len() as u64 + approvals)?;
    Some(Summary { place, waiting, approvals, count })
}

/// A waiting session in the menu: its title, and what it waits for when that is known.
pub fn label_of(w: &Waiting) -> String {
    let title = if w.title.is_empty() { "A session" } else { w.title.as_str() };
    let text = if w.note.is_empty() { title.to_string() } else { format!("{title} \u{b7} {}", w.note) };
    let mut out: String = text.chars().take(72).collect();
    if text.chars().count() > 72 {
        out.push('\u{2026}');
    }
    out
}

/// What a menu item asks for: the session named in its id, the approvals, the window, or quitting.
#[derive(Debug, PartialEq)]
pub enum Chosen {
    Open,
    Quit,
    Approvals,
    Session(String),
    Nothing,
}

pub fn chosen_of(id: &str) -> Chosen {
    match id {
        "open" => Chosen::Open,
        "quit" => Chosen::Quit,
        "approvals" => Chosen::Approvals,
        _ => match id.strip_prefix("session:") {
            Some(s) if is_session(s) => Chosen::Session(s.into()),
            _ => Chosen::Nothing,
        },
    }
}

fn plural(n: u64, one: &str, many: &str) -> String {
    format!("{n} {}", if n == 1 { one } else { many })
}

fn menu_for(app: &AppHandle, summary: Option<&Summary>) -> tauri::Result<Menu<Wry>> {
    let mut items: Vec<Box<dyn IsMenuItem<Wry>>> = vec![
        Box::new(MenuItem::with_id(app, "open", format!("Open {TITLE}"), true, None::<&str>)?),
        Box::new(PredefinedMenuItem::separator(app)?),
    ];
    match summary {
        Some(s) if s.count > 0 => {
            items.push(Box::new(MenuItem::with_id(app, "heading", format!("Needs you, in {}", s.place), false, None::<&str>)?));
            for w in &s.waiting {
                items.push(Box::new(MenuItem::with_id(app, format!("session:{}", w.session), label_of(w), true, None::<&str>)?));
            }
            let unlisted = s.count.saturating_sub(s.waiting.len() as u64 + s.approvals);
            if unlisted > 0 {
                items.push(Box::new(MenuItem::with_id(app, "more", format!("and {} more", plural(unlisted, "session", "sessions")), false, None::<&str>)?));
            }
            if s.approvals > 0 {
                items.push(Box::new(MenuItem::with_id(app, "approvals", format!("{} asked", plural(s.approvals, "approval", "approvals")), true, None::<&str>)?));
            }
        }
        Some(s) => items.push(Box::new(MenuItem::with_id(app, "heading", format!("Nothing needs you in {}", s.place), false, None::<&str>)?)),
        None => items.push(Box::new(MenuItem::with_id(app, "heading", "Nothing needs you", false, None::<&str>)?)),
    }
    items.push(Box::new(PredefinedMenuItem::separator(app)?));
    items.push(Box::new(MenuItem::with_id(app, "quit", format!("Quit {TITLE}"), true, None::<&str>)?));
    let refs: Vec<&dyn IsMenuItem<Wry>> = items.iter().map(|b| b.as_ref()).collect();
    Menu::with_items(app, &refs)
}

/// The main window, shown and in front.
pub fn show_main(app: &AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

fn chosen(app: &AppHandle, id: &str) {
    match chosen_of(id) {
        Chosen::Open => show_main(app),
        Chosen::Quit => app.exit(0),
        Chosen::Approvals => {
            show_main(app);
            let _ = app.emit_to("main", "menu-bar-open", serde_json::json!({ "approvals": true }));
        }
        Chosen::Session(session) => {
            show_main(app);
            let _ = app.emit_to("main", "menu-bar-open", serde_json::json!({ "session": session }));
        }
        Chosen::Nothing => {}
    }
}

/// Make the icon, with nothing listed until the page says what needs the person.
pub fn make(app: &tauri::App) -> tauri::Result<TrayIcon> {
    let menu = menu_for(app.handle(), None)?;
    log::debug!("menu bar: icon made");
    TrayIconBuilder::with_id(ICON_ID)
        .icon(tauri::include_image!("icons/menu-bar.png"))
        // a template image: the system draws it in the menu bar's own colour, light or dark
        .icon_as_template(true)
        .tooltip(TITLE)
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| chosen(app, event.id().as_ref()))
        .build(app)
}

/// Whether the app has its icon in the menu bar, so that closing the window can keep it there.
pub fn shown(app: &AppHandle) -> bool {
    app.try_state::<MenuBar>().and_then(|s| s.0.lock().ok().map(|g| g.is_some())).unwrap_or(false)
}

/// How often the page is asked to read again while its window is closed to the menu bar (seconds).
pub const HIDDEN_EVERY: u64 = 15;
/// What the page runs when asked (ui/src/menuBar.ts defines it): read its sessions and what waits for the person.
pub const TICK: &str = "window.__ppMenuBarTick && window.__ppMenuBarTick()";

/// While the window is closed to the menu bar, ask the page to read again every HIDDEN_EVERY seconds, so the count and
/// the menu stay true: a hidden page's own timers may be slowed or held by WebKit, and this one runs whatever they do
/// (the page reads while visible by itself, so nothing is asked of it then).
pub fn keep_reading(app: AppHandle) {
    std::thread::spawn(move || loop {
        std::thread::sleep(std::time::Duration::from_secs(HIDDEN_EVERY));
        if !shown(&app) {
            continue;
        }
        if let Some(w) = app.get_webview_window("main") {
            if !w.is_visible().unwrap_or(true) || w.is_minimized().unwrap_or(false) {
                if let Err(e) = w.eval(TICK) {
                    log::debug!("menu bar: the page could not be asked to read: {e}");
                }
            }
        }
    });
}

/// The page says what needs the person in the place the window shows; the menu, the tooltip and the count beside the
/// icon follow it.
#[tauri::command]
pub fn menu_bar(app: AppHandle, summary: serde_json::Value) -> Result<(), String> {
    let summary = summary_of(&summary).ok_or("That is not what the menu bar shows.")?;
    let state = app.state::<MenuBar>();
    let guard = state.0.lock().map_err(|_| "The menu bar is not available.".to_string())?;
    let Some(icon) = guard.as_ref() else { return Ok(()) };
    icon.set_menu(Some(menu_for(&app, Some(&summary)).map_err(|e| e.to_string())?)).map_err(|e| e.to_string())?;
    let tip = if summary.count > 0 { format!("{TITLE}: {} in {}", plural(summary.count, "thing needs you", "things need you"), summary.place) } else { TITLE.to_string() };
    icon.set_tooltip(Some(tip)).map_err(|e| e.to_string())?;
    #[cfg(target_os = "macos")]
    icon.set_title(if summary.count > 0 { Some(summary.count.to_string()) } else { None }).map_err(|e| e.to_string())?;
    // counts only: what the sessions are stays in the menu
    log::debug!("menu bar: {} listed, {} approvals, {} in all", summary.waiting.len(), summary.approvals, summary.count);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn one(session: &str) -> serde_json::Value {
        json!({ "session": session, "title": "Fix the login", "note": "Wants to use Bash" })
    }

    #[test]
    fn a_summary_is_taken_as_the_page_gives_it_and_its_text_kept_to_one_line() {
        let s = summary_of(&json!({ "place": "This Mac", "waiting": [one("0123456789ab"),
            { "session": "abcdefabcdef", "title": "line one\nline two\u{7}", "note": "" }], "approvals": 2, "count": 5 })).unwrap();
        assert_eq!(s.place, "This Mac");
        assert_eq!(s.waiting[0], Waiting { session: "0123456789ab".into(), title: "Fix the login".into(), note: "Wants to use Bash".into() });
        assert_eq!(s.waiting[1].title, "line one line two");
        assert_eq!((s.approvals, s.count), (2, 5));
        let long = summary_of(&json!({ "place": "p".repeat(100), "waiting": [{ "session": "0123456789ab", "title": "t".repeat(200), "note": "n".repeat(200) }],
                                       "approvals": 0, "count": 1 })).unwrap();
        assert_eq!((long.place.chars().count(), long.waiting[0].title.chars().count(), long.waiting[0].note.chars().count()), (60, 80, 80));
    }

    #[test]
    fn what_is_not_a_summary_is_refused() {
        let good = json!({ "place": "This Mac", "waiting": [one("0123456789ab")], "approvals": 0, "count": 1 });
        assert!(summary_of(&good).is_some());
        let bad = [
            json!({ "place": "", "waiting": [], "approvals": 0, "count": 0 }),
            json!({ "place": "This Mac", "waiting": [one("../../etc")], "approvals": 0, "count": 1 }),
            json!({ "place": "This Mac", "waiting": [one("0123456789AB")], "approvals": 0, "count": 1 }),
            json!({ "place": "This Mac", "waiting": (0..11).map(|_| one("0123456789ab")).collect::<Vec<_>>(), "approvals": 0, "count": 11 }),
            json!({ "place": "This Mac", "waiting": [one("0123456789ab")], "approvals": 1, "count": 1 }),
            json!({ "place": "This Mac", "waiting": [], "approvals": -1, "count": 0 }),
            json!({ "place": "This Mac", "waiting": [], "approvals": 0, "count": 1_000_000 }),
            json!({ "place": "This Mac", "waiting": [{ "session": "0123456789ab", "title": 7, "note": "" }], "approvals": 0, "count": 1 }),
            json!("This Mac"),
        ];
        for b in bad {
            assert!(summary_of(&b).is_none(), "{b}");
        }
    }

    #[test]
    fn a_session_is_listed_by_its_title_and_what_it_waits_for_cut_to_fit() {
        let w = |title: &str, note: &str| Waiting { session: "0123456789ab".into(), title: title.into(), note: note.into() };
        assert_eq!(label_of(&w("Fix the login", "Wants to use Bash")), "Fix the login \u{b7} Wants to use Bash");
        assert_eq!(label_of(&w("Fix the login", "")), "Fix the login");
        assert_eq!(label_of(&w("", "Asks a question")), "A session \u{b7} Asks a question");
        let long = label_of(&w(&"x".repeat(80), &"y".repeat(80)));
        assert_eq!(long.chars().count(), 73);
        assert!(long.ends_with('\u{2026}'));
    }

    #[test]
    fn a_menu_item_asks_for_what_its_id_names_and_nothing_else() {
        assert_eq!(chosen_of("open"), Chosen::Open);
        assert_eq!(chosen_of("quit"), Chosen::Quit);
        assert_eq!(chosen_of("approvals"), Chosen::Approvals);
        assert_eq!(chosen_of("session:0123456789ab"), Chosen::Session("0123456789ab".into()));
        for other in ["session:", "session:0123", "session:0123456789ab/../x", "heading", "more", ""] {
            assert_eq!(chosen_of(other), Chosen::Nothing, "{other}");
        }
    }
}
