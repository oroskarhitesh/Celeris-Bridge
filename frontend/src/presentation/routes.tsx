/**
 * Every route, in one place, under one layout.
 *
 * Later chunks add a child route each and an entry in ``navigation.ts``. The
 * agent detail page is reached from the list rather than from the navigation,
 * so it adds a route and no destination.
 */

import { createBrowserRouter } from "react-router-dom";

import { AppShell } from "@/presentation/components/layout/AppShell";
import { AgentPage } from "@/presentation/pages/AgentPage";
import { AgentsPage } from "@/presentation/pages/AgentsPage";
import { ConversationsPage } from "@/presentation/pages/ConversationsPage";
import { KnowledgePage } from "@/presentation/pages/KnowledgePage";
import { LeadsPage } from "@/presentation/pages/LeadsPage";
import { NewAgentPage } from "@/presentation/pages/NewAgentPage";
import { NotFoundPage } from "@/presentation/pages/NotFoundPage";
import { OverviewPage } from "@/presentation/pages/OverviewPage";
import { TranscriptPage } from "@/presentation/pages/TranscriptPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "agents", element: <AgentsPage /> },
      // Before the ":id" route: "new" must not be read as an agent id.
      { path: "agents/new", element: <NewAgentPage /> },
      { path: "agents/:id", element: <AgentPage /> },
      { path: "knowledge", element: <KnowledgePage /> },
      { path: "conversations", element: <ConversationsPage /> },
      { path: "conversations/:id", element: <TranscriptPage /> },
      { path: "leads", element: <LeadsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
