/**
 * pages/UpdatePasswordPage.jsx — the password reset form behind `/reset-password`.
 *
 * retail-ui-simplification task 7.5 (commit 11). Requirements 4.1, 4.2, 4.3, 4.4, 5.1, 6.2.
 * design.md §2.4.
 *
 * 3 absolute pixel sizes to 0, the entry deleted. **No colour-literal entry, and none is
 * added** — this file was already at zero, so the colour half of task 7's step 5 is the
 * *absence* of a new entry rather than a number coming down.
 *
 * THE CHEAPEST TEST OF THE CONVENTION, WHICH IS WHY IT IS WORTH WRITING DOWN WHAT FITS
 * ----------------------------------------------------------------------------------
 * 79 lines, one field, one command, two messages. Requirement 4.5 puts it fourth precisely so
 * the convention meets a file with nothing to hide behind, and three of its five steps turn
 * out to be answered by "there is nothing here of that kind" — which is a finding rather than
 * a shortcut, so each one is stated:
 *
 *   **Step 1, `usePanelState`: THERE IS NO READ ON THIS PAGE.** The only call it makes is
 *   `supabase.auth.updateUser`, a MUTATION. `usePanelState` drives a reader and may re-issue
 *   it; pointing it at a password change would be a hook that can resubmit one. So the
 *   `loading`/`error` pair here is a mutation's in-flight and failure state, and the
 *   primitives that model those are `ds/CommandButton`'s `loading` and `ds/Alert` — which is
 *   what they now are. Requirement 4.1 is about reads and is satisfied vacuously.
 *
 *   **Step 2, `pageFields.js`: THERE IS NO FIGURE ON THIS PAGE.** No count, no balance, no
 *   timestamp — nothing with a not-available arm. Declaring an entry would put a page in
 *   `PAGE_FIELDS_BY_PAGE` with nothing to declare, which makes the audit look complete where
 *   it is empty. Same argument as `pages/TwoFA.jsx`.
 *
 *   **Step 5, colour: it was already at zero.** Every surface, line and content colour already
 *   read `design/tokens.js`. Two constructs the guard cannot see did go, though, and they are
 *   the kind worth recognising: `` `${token.status.profit.fg}12` `` — a token with two hex
 *   digits concatenated onto it, i.e. a hand-mixed 7% alpha wearing a token's name, carrying no
 *   `#` so `no-colour-literals` never counted it — and the untokenised `borderRadius: 18`,
 *   which is off the declared radius scale entirely (`--radius-xl` stops at 12). Both left with
 *   the elements that held them.
 *
 * THE 3 PIXEL SIZES, BY §2.4'S NINE QUESTIONS
 * -------------------------------------------
 *   22 ×1  the `<h1>`               Q8 heading, page level  → `--text-page`
 *   13 ×1  the success message      Q1 sentence             → `ds/Alert`; removed
 *   12 ×1  the failure message      Q1 sentence             → `ds/Alert`; removed
 *
 * Two of the three are the same construct twice — a sentence in a hand-built coloured strip —
 * and both become `ds/Alert`, which derives its hue, its icon and its live-region role from
 * `severity` and reads a declared step. `ds/Alert` has no `success` severity and one is not
 * added to a shared primitive from inside a page commit, so the confirmation reports at `info`
 * with its sentence unchanged. That is the same resolution `pages/TwoFA.jsx` took, so the two
 * auth surfaces do not diverge on one question.
 *
 * ONE DEFECT FIXED IN PASSING, NAMED RATHER THAN SLIPPED IN
 * -------------------------------------------------------
 * **The submit button did not submit.** `components/ui/Button` renders `<button type="button">`
 * unless a `type` is passed through, and this page passed none — so clicking *Update Password*
 * ran nothing at all, and the only way to change a password was to press Enter inside the
 * field (implicit submission, which works because the form has a single qualifying input).
 * `ds/CommandButton` takes an `onClick`, so the pointer path now runs the same handler the
 * keyboard path runs. No request changed: the one auth call below is untouched — the same two
 * arguments in the same positions, with `emailRedirectTo` in the second — and
 * `authRedirectOrigin.test.jsx` asserts exactly that, one call per submit with the redirect
 * built by `getAuthRedirectUrl` and not by a literal. That test also scans `src/` for the call
 * SITES, so this note names the method rather than spelling the expression: a second
 * occurrence in prose would read as a second call site.
 *
 * `ds/Field` REPLACES `common/primitives`' `Inp`, AND LOSES ONE DECORATION
 * ---------------------------------------------------------------------
 * `Inp` is a previous-generation shim whose own label is 9px monospace uppercase with
 * `letterSpacing: 1.5` — four treatments on two words, and one of the twelve sizes
 * `components/common/primitives.jsx` is budgeted for and resting at. `ds/Field` renders a
 * visible `<label htmlFor>` at a declared step, and it is what every migrated page's form
 * controls read. The one thing that does not survive is `Inp`'s leading `Lock` glyph: `ds/Field`
 * has no icon slot, and a padlock beside a field labelled *New Password* on a page titled
 * *Reset Password* is the third statement of one fact. No information is lost with it.
 */

import React, { useState } from "react";
import { useNavigate } from "react-router-dom";

import { Alert } from "../components/ds/Alert";
import { CommandButton } from "../components/ds/CommandButton";
import { Field } from "../components/ds/Field";
import { token } from "../design/tokens";
// The one auth-redirect origin derivation (see `config.js`).
import { getAuthRedirectUrl } from "../config";

export default function UpdatePasswordPage() {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState(false);

  const handleUpdate = async (e) => {
    if (e) e.preventDefault();
    if (!password) { setError("Password cannot be empty."); return; }
    setLoading(true); setError(""); setSuccess(false);

    try {
      const { supabase } = await import('../supabase');
      // Second argument, same reason as `App.jsx`'s copy of this screen: it is where
      // `emailRedirectTo` lives, so an email change cannot fall back to the Site URL.
      const { error } = await supabase.auth.updateUser(
        { password },
        { emailRedirectTo: getAuthRedirectUrl() },
      );

      if (error) throw error;

      setSuccess(true);
      setTimeout(() => navigate("/app/dashboard"), 2000);
    } catch (err) {
      setError(err.message || "Failed to update password.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ background: token.surface.canvas, minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div
        className="w-full max-w-md rounded-xl border border-line-default p-9"
        style={{ background: token.surface.raised }}
      >
        {/* fontSize: 22 → --text-page (heading — Q8, the page's own <h1>). */}
        <h1 className="mb-5 text-center text-page font-semibold text-content-primary">
          Reset Password
        </h1>
        {success ? (
          /* fontSize: 13 → the primitive's step. `ds/Alert` has no `success` severity and one
             is not added to a shared primitive from a page commit, so the confirmation reports
             at `info` and its sentence says what happened. */
          <Alert severity="info" title="Password updated! Redirecting..." />
        ) : (
          <form onSubmit={handleUpdate} className="flex flex-col gap-3.5">
            <Field
              id="new-password"
              label="New Password"
              placeholder="•••••••••••"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              disabledReason="A password change is in flight."
              required
            />
            {/* The pointer path now runs the same handler the keyboard path runs — see the
                header on why it previously ran nothing. The form keeps its `onSubmit`, so
                Enter inside the field is unchanged. */}
            <CommandButton
              intent="primary"
              onClick={handleUpdate}
              loading={loading}
              loadingLabel="Updating..."
              className="mt-2 w-full justify-center"
            >
              Update Password
            </CommandButton>
            {/* fontSize: 12 → the primitive's step. Every message the form can render arrives
                here, unchanged: the empty-password check, and whatever the auth service
                reported about a refused change. */}
            {!!error && <Alert severity="error" title={error} className="mt-2.5" />}
          </form>
        )}
      </div>
    </div>
  );
}
