import type { ReactNode } from "react";
import { CopilotKitProvider } from "@copilotkit/react-core/v2";
import { HttpAgent } from "@ag-ui/client";

import { AGENT_ID, BACKEND_URL } from "./config";

// Connect the browser DIRECTLY to the Python FastAPI + AG-UI endpoint — no Node
// CopilotRuntime. `agents__unsafe_dev_only` is the shipped no-Node dev path; for
// production switch to `selfManagedAgents` and secure the FastAPI endpoint
// (auth / CORS / rate limiting) yourself. The agent instance is created once at
// module scope so its identity is stable across renders.
const demoAgent = new HttpAgent({ url: `${BACKEND_URL}/agent` });

export function Providers({ children }: { children: ReactNode }) {
  return (
    <CopilotKitProvider agents__unsafe_dev_only={{ [AGENT_ID]: demoAgent }}>
      {children}
    </CopilotKitProvider>
  );
}
