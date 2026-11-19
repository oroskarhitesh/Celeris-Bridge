/**
 * Chunk one's only page: proof that the two layers are wired together.
 *
 * Everything on it comes through the data layer -- a query hook and the
 * session slice -- and nothing on it knows a URL or a header.
 */

import { useAppSelector } from "@/data/store/hooks";
import { selectToken } from "@/data/store/slices/sessionSlice";
import { TokenForm } from "@/presentation/components/session/TokenForm";
import { BackendStatus } from "@/presentation/components/system/BackendStatus";
import { CredentialStatus } from "@/presentation/components/system/CredentialStatus";
import { Card } from "@/presentation/components/ui/Card";

export function OverviewPage() {
  const token = useAppSelector(selectToken);

  return (
    <div className="mx-auto grid max-w-4xl gap-6 md:grid-cols-2">
      <Card title="Connection" description="Unauthenticated, so it tests the wiring only.">
        <BackendStatus />
      </Card>
      <Card title="Credential" description="One authenticated read, to see if the token works.">
        <div className="space-y-4">
          <CredentialStatus token={token} />
          <TokenForm />
        </div>
      </Card>
    </div>
  );
}
