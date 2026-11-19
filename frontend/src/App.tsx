/**
 * The two providers, and nothing else.
 *
 * Keeping this file this small is deliberate: the store and the router are
 * the only things the whole tree depends on, so anything else that ends up
 * here is something that should have been a component.
 */

import { Provider } from "react-redux";
import { RouterProvider } from "react-router-dom";

import { store } from "@/data/store/store";
import { router } from "@/presentation/routes";

export function App() {
  return (
    <Provider store={store}>
      <RouterProvider router={router} />
    </Provider>
  );
}
