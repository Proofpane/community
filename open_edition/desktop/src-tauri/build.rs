fn main() {
    // the app's own commands, each allowed by name in a capability: the main window's (capabilities/default.json), a
    // preview window's pages, which are this Mac's own servers and may call design_pick alone (capabilities/preview.json),
    // and a pop-out's, the app's own page, which may ask for this Mac's server and a hosted sign-in (capabilities/popout.json)
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["local_server", "open_preview", "design_pick", "tray_status", "tray_approve",
                                                "token_get", "token_set", "menu_bar", "pick_folder", "editors",
                                                "open_worktree", "open_popout"]),
    ))
    .expect("the app's build failed");
}
