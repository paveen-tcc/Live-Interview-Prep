# Browser compatibility and accessibility matrix

Status: automated semantics and text-mode flows pass locally. Physical voice-device rows require manual execution on current stable browsers before private alpha.

| Check | Chrome | Safari | Edge | Automated |
| --- | --- | --- | --- | --- |
| Dashboard, setup, scorecard, report | Pending current-stable smoke | Pending current-stable smoke | Pending current-stable smoke | React tests + local browser pass |
| Developer text input; no media-input permission | Pending current-stable smoke | Pending current-stable smoke | Pending current-stable smoke | React/API/local browser pass |
| Voice preflight and microphone permission | Pending physical test | Pending physical test | Pending physical test | Permission gating unit-tested |
| 15-minute Realtime voice interview | Pending physical test | Pending physical test | Pending physical test | Mocked session/reconnect tests only |
| Reconnect without duplicate typed answer | Pending network shaping | Pending network shaping | Pending network shaping | API/browser logic tests pass |
| Evidence report and delivery separation | Pending current-stable smoke | Pending current-stable smoke | Pending current-stable smoke | Evaluator, API, React, local browser pass |
| Keyboard navigation and visible focus | Pending manual pass | Pending manual pass | Pending manual pass | Semantic role tests and focus styles pass |
| 200% zoom and narrow viewport | Pending manual pass | Pending manual pass | Pending manual pass | Responsive layouts implemented |
| Reduced motion | Pending manual pass | Pending manual pass | Pending manual pass | CSS media query implemented |
| Screen reader (VoiceOver/NVDA) | VoiceOver pending | VoiceOver pending | NVDA pending | Landmarks, live states, labels, dialogs tested structurally |

## Manual release procedure

1. Use current stable Chrome, Safari, and Edge on a supported desktop OS.
2. Run one text-mode interview and confirm no camera/microphone prompt or media-input track.
3. With permission to speak, run a 15-minute voice interview using headphones. Record connection success, reconnects, end-of-speech-to-first-audio samples, and completion state.
4. Repeat under the agreed packet-loss/jitter profile.
5. Complete keyboard-only, 200% zoom, reduced-motion, and screen-reader checks.
6. Record browser versions, OS, network profile, result, and issue IDs in the pilot log. Do not write résumé or transcript content into the log.
