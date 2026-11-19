import { Button } from "@/presentation/components/ui/Button";
import { useAppDispatch, useAppSelector } from "@/data/store/hooks";
import { selectSignedIn, signedOut } from "@/data/store/slices/sessionSlice";

export function SignOutButton() {
  const signedIn = useAppSelector(selectSignedIn);
  const dispatch = useAppDispatch();
  if (!signedIn) return null;
  return (
    <Button look="ghost" onClick={() => dispatch(signedOut())}>
      Sign out
    </Button>
  );
}
