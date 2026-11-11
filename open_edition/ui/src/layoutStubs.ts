// The layout cases' page (layoutHarness.tsx), loaded before anything it draws: a browser that can take speech, so the
// chat box draws its microphone button too, as on a phone that can, and its row is measured with every button it has.
// Never part of the app's build.
(window as unknown as { webkitSpeechRecognition: unknown }).webkitSpeechRecognition = class {};
