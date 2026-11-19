import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <p className="text-sm font-semibold text-ink">No such page</p>
      <p className="mt-1 text-sm text-ink-soft">
        The address is wrong, not the server.
      </p>
      <Link to="/" className="mt-4 inline-block text-sm font-medium text-brand">
        Back to the overview
      </Link>
    </div>
  );
}
