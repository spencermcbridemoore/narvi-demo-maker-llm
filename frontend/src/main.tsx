import { lazy, Suspense } from "react";
import { createRoot } from "react-dom/client";

import { Providers } from "./providers";
import App from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

// The rubric grader lives at /grader in the same SPA (see src/grader). It is
// lazy-loaded so DemoBuilder's bundle is unchanged, and it doesn't need the
// CopilotKit provider.
const GraderApp = lazy(() => import("./grader/GraderApp"));
const isGrader = window.location.pathname.replace(/\/+$/, "").startsWith("/grader");

// NOTE: intentionally no <StrictMode> here. StrictMode double-invokes effects in
// dev, which would start the agent run twice on the same thread. Re-enable once
// the run-start is made idempotent against the double-mount.
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    {isGrader ? (
      <Suspense fallback={null}>
        <GraderApp />
      </Suspense>
    ) : (
      <Providers>
        <App />
      </Providers>
    )}
  </ErrorBoundary>,
);
