import { NavLink } from "react-router-dom";

import { DESTINATIONS } from "./navigation";

export function SideNav() {
  return (
    <nav className="flex flex-col gap-1 p-3">
      {DESTINATIONS.map((destination) => (
        <NavLink
          key={destination.to}
          to={destination.to}
          end={destination.to === "/"}
          className={({ isActive }) =>
            `rounded-lg px-3 py-2 text-sm font-medium transition ${
              isActive
                ? "bg-brand-soft text-brand"
                : "text-ink-soft hover:bg-canvas hover:text-ink"
            }`
          }
        >
          {destination.label}
        </NavLink>
      ))}
    </nav>
  );
}
