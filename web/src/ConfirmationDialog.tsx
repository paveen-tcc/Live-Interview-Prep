import { useEffect, useRef, useState } from "react";

interface ConfirmationDialogProps {
  title: string;
  body: string;
  confirmLabel: string;
  confirmationPhrase?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

export function ConfirmationDialog({
  title,
  body,
  confirmLabel,
  confirmationPhrase,
  busy = false,
  onCancel,
  onConfirm,
}: ConfirmationDialogProps) {
  const [confirmation, setConfirmation] = useState("");
  const phraseInput = useRef<HTMLInputElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const confirmed = !confirmationPhrase || confirmation === confirmationPhrase;

  useEffect(() => {
    (phraseInput.current ?? cancelButton.current)?.focus();
  }, []);

  return (
    <div
      className="dialog-backdrop"
      role="presentation"
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onCancel();
      }}
    >
      <section
        className="card confirmation-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirmation-dialog-title"
        aria-describedby="confirmation-dialog-body"
      >
        <p className="section__eyebrow">Permanent action</p>
        <h2 id="confirmation-dialog-title">{title}</h2>
        <p id="confirmation-dialog-body">{body}</p>
        {confirmationPhrase ? (
          <label className="field confirmation-dialog__field">
            <span>{`Type "${confirmationPhrase}" to confirm`}</span>
            <input
              ref={phraseInput}
              className="input"
              autoComplete="off"
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
        ) : null}
        <div className="confirmation-dialog__actions">
          <button
            ref={cancelButton}
            className="btn btn--ghost"
            type="button"
            disabled={busy}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            className="btn btn--danger"
            type="button"
            disabled={!confirmed || busy}
            onClick={onConfirm}
          >
            {busy ? "Deleting…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}
