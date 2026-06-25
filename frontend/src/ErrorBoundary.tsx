import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

type Props = { children: ReactNode };
type State = { error: Error | null };

// Surfaces render errors in the DOM instead of leaving a blank page.
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("App crashed:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div
          role="alert"
          style={{
            padding: 24,
            fontFamily: "system-ui, sans-serif",
            color: "#fca5a5",
            background: "#0b1020",
            height: "100%",
            overflow: "auto",
          }}
        >
          <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
          <pre style={{ whiteSpace: "pre-wrap" }}>{String(this.state.error?.stack ?? this.state.error)}</pre>
        </div>
      );
    }
    return this.props.children;
  }
}
