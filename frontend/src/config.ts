// Backend base URL (FastAPI + AG-UI). Override with VITE_BACKEND_URL in a
// frontend/.env.local if your backend runs elsewhere.
export const BACKEND_URL =
  import.meta.env.VITE_BACKEND_URL ?? "http://localhost:8000";

// Client-side agent id. CopilotKit resolves the *active* agent for hooks like
// useAgent/useInterrupt by this id, defaulting to "default" — so a single-agent
// app must register it under "default". (Independent of the backend's
// LangGraphAgent name, which only labels the /agent endpoint.)
export const AGENT_ID = "default";
