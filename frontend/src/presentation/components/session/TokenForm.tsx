/**
 * Paste a bearer token.
 *
 * A stand-in for a sign-in screen, and honest about it: there are no users in
 * the backend yet, only tokens that name a workspace. When real
 * authentication arrives this is the one component that is deleted, because
 * everything else reads the token from the session slice rather than from
 * here.
 */

import { useState } from "react";

import { useAppDispatch, useAppSelector } from "@/data/store/hooks";
import { selectToken, tokenSet } from "@/data/store/slices/sessionSlice";
import { Button } from "@/presentation/components/ui/Button";
import { TextInput } from "@/presentation/components/ui/TextInput";

export function TokenForm() {
  const current = useAppSelector(selectToken);
  const dispatch = useAppDispatch();
  const [draft, setDraft] = useState("");

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        dispatch(tokenSet(draft));
        setDraft("");
      }}
    >
      <TextInput
        label="API token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        value={draft}
        placeholder={current ? "A token is already set" : "Paste a token"}
        onChange={(event) => setDraft(event.target.value)}
        hint="Kept for this tab only. The workspace comes from the token, never from this app."
      />
      <Button type="submit" look="primary" disabled={!draft.trim()}>
        Use this token
      </Button>
    </form>
  );
}
