import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
    this.props.onError?.(error, info);
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      return (
        <div style={{
          padding: "12px 16px",
          background: "rgba(239, 68, 68, 0.08)",
          border: "1px solid rgba(239, 68, 68, 0.2)",
          borderRadius: 10,
          fontSize: 13,
          color: "var(--danger, #ef4444)",
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>渲染异常</div>
          <div style={{ opacity: 0.7, fontSize: 12, wordBreak: "break-word" }}>
            {this.state.error?.message || "未知错误"}
          </div>
          <button
            onClick={() => this.setState({ hasError: false, error: null })}
            style={{
              marginTop: 8, padding: "4px 12px", fontSize: 12, fontWeight: 600,
              borderRadius: 6, border: "1px solid rgba(239, 68, 68, 0.3)",
              background: "transparent", color: "var(--danger, #ef4444)", cursor: "pointer",
            }}
          >
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
