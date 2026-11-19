/**
 * Render the way the application does: one fresh store, one router.
 *
 * Fresh because the query cache lives in the store, so a shared one would let
 * a test pass on the previous test's answers.
 */

import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { makeStore } from "@/data/store/store";
import { ToastStack } from "@/presentation/components/ui/ToastStack";

interface Options {
  token?: string;
  /** The address to start at, for a page that reads the URL. */
  at?: string;
  /**
   * A route pattern to mount the element under, for a page that reads a path
   * parameter -- ``"/agents/:id"``. Without one the element is rendered
   * directly, which is all a page that only reads the search string needs.
   */
  path?: string;
}

export function renderPage(
  element: ReactElement,
  { token = "", at = "/", path }: Options = {},
) {
  const store = makeStore({ session: { token } });
  const view = render(
    <Provider store={store}>
      <MemoryRouter initialEntries={[at]}>
        {path ? (
          <Routes>
            <Route path={path} element={element} />
          </Routes>
        ) : (
          element
        )}
        {/* The shell mounts this in the real application, so a test that
            asserts on what a mutation said has somewhere to read it. */}
        <ToastStack />
      </MemoryRouter>
    </Provider>,
  );
  return { ...view, store };
}
