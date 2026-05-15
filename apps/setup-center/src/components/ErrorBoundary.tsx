import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  /** Optional human label appearing in the default fallback (e.g. "组织监控面板"). */
  panelName?: string;
  onError?: (error: Error, info: ErrorInfo) => void;
  /**
   * When any value in this array changes between renders, the boundary
   * automatically clears its error state. Use it to recover when the user
   * switches tabs / orgs without forcing a full unmount.
   */
  resetKeys?: ReadonlyArray<unknown>;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

function shallowArrayEqual(a: ReadonlyArray<unknown>, b: ReadonlyArray<unknown>): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (!Object.is(a[i], b[i])) return false;
  }
  return true;
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

  componentDidUpdate(prevProps: Props) {
    const prevKeys = prevProps.resetKeys ?? [];
    const nextKeys = this.props.resetKeys ?? [];
    if (
      this.state.hasError &&
      (prevKeys.length > 0 || nextKeys.length > 0) &&
      !shallowArrayEqual(prevKeys, nextKeys)
    ) {
      this.setState({ hasError: false, error: null });
    }
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;
      const label = this.props.panelName ? `${this.props.panelName}渲染异常` : "渲染异常";
      return (
        <div style={{
          padding: "12px 16px",
          background: "rgba(239, 68, 68, 0.08)",
          border: "1px solid rgba(239, 68, 68, 0.2)",
          borderRadius: 10,
          fontSize: 13,
          color: "var(--danger, #ef4444)",
        }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
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
