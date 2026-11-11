// The phone app runs from lib.rs (its mobile entry); this lets `cargo build` check it on a desktop too.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    proofpane_community_phone_lib::run()
}
