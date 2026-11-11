// Before the phone app approves something on the person's behalf (a chat's tool call, a held command, another device's
// sign-in), the person is asked for Face ID, Touch ID or the phone's passcode: a phone left unlocked on a table is not
// the person. In the browser and the desktop app the click is the person's, as before; so it is on a phone with no
// passcode set, where there is nothing to ask. A cancel or a failure stops the approval.

const PHONE = import.meta.env.MODE === "phone";
// why the plugin cannot ask, when nothing could be asked at all
const NOTHING_TO_ASK = new Set(["passcodeNotSet"]);

type Biometric = typeof import("@tauri-apps/plugin-biometric");

export async function confirmPresence(reason: string, phone: boolean = PHONE,
                                      load: () => Promise<Biometric> = () => import("@tauri-apps/plugin-biometric")): Promise<void> {
  if (!phone) return;
  const biometric = await load();
  const status = await biometric.checkStatus();
  if (!status.isAvailable && status.errorCode && NOTHING_TO_ASK.has(status.errorCode)) return;
  try {
    await biometric.authenticate(reason, { allowDeviceCredential: true, cancelTitle: "Not now" });
  } catch {
    throw new Error("Not approved: Face ID, Touch ID or the passcode was not given.");
  }
}
