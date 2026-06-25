import { createRoot } from "react-dom/client";

import { Providers } from "./providers";
import App from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

// NOTE: intentionally no <StrictMode> here. StrictMode double-invokes effects in
// dev, which would start the agent run twice on the same thread. Re-enable once
// the run-start is made idempotent against the double-mount.
createRoot(document.getElementById("root")!).render(
  <ErrorBoundary>
    <Providers>
      <App />
    </Providers>
  </ErrorBoundary>,
);
