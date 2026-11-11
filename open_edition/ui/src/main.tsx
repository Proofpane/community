import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./theme";        // sets the theme on <html> before the first paint of the app
import { DESKTOP, takeSignIn } from "./api";
import { DesktopStart } from "./components/DesktopStart";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Root } from "./root";

if (!DESKTOP) takeSignIn();   // a browser opened by `proofpane_open.desktop --open` carries its sign-in

// the page is the app, around which the end-to-end encryption keys this tab unlocks live, or a pop-out of the control
// view's map (root.tsx)
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary what="The ADE">
      {DESKTOP ? <DesktopStart><Root /></DesktopStart> : <Root />}
    </ErrorBoundary>
  </StrictMode>,
);
