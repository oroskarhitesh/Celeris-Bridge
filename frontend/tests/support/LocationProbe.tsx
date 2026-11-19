/**
 * Where the router thinks it is, rendered as text.
 *
 * Navigation is part of what a page does -- "created, now show me the agent"
 * -- so it should be assertable without mocking the router.
 */

import { useLocation } from "react-router-dom";

export function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{pathname + search}</output>;
}
