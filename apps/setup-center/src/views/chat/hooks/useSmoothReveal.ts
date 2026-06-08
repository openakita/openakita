import { useEffect, useRef, useState } from "react";

// 流式文本的匀速逐字显示：把"网络突发到达"和"视觉显示节奏"解耦，让文字像
// 打字机一样平滑流出，而不是一坨一坨地蹦（SSE 经常一次推一大段）。
//
// 思路（参考成熟桌面端实现）：维护一个目标串 target 和已显示串 shown，每帧
// 按"剩余量比例"补一小段，使整段在 ~REVEAL_DRAIN_MS 内追平；每帧上限
// REVEAL_MAX_CHARS_PER_FRAME 防止一次巨量 dump 直接糊成一整块。循环以
// "是否还有积压"为条件而非 isRunning——这样流在揭示途中结束时，会把尾巴
// 继续平滑放完，而不是"啪"地一下补全。
const REVEAL_DRAIN_MS = 500;
const REVEAL_MAX_CHARS_PER_FRAME = 30;

/**
 * 把 `text` 以匀速方式逐步揭示出来。`isRunning` 为 true（流式进行中）时，
 * 新挂载从空串开始揭示；为 false（历史消息）时直接整段显示、无动画。
 */
export function useSmoothReveal(text: string, isRunning: boolean): string {
  const [displayed, setDisplayed] = useState(isRunning ? "" : text);
  const targetRef = useRef(text);
  const shownRef = useRef(displayed);
  const frameRef = useRef<number | null>(null);
  const lastTickRef = useRef(0);

  shownRef.current = displayed;
  targetRef.current = text;

  useEffect(() => {
    if (typeof window === "undefined") return;

    // 非追加式变更（重新生成 / 切换分支 / 历史回放）：流式中从空串重揭示，
    // 否则直接贴成替换后的内容。
    if (!text.startsWith(shownRef.current)) {
      shownRef.current = isRunning ? "" : text;
      setDisplayed(shownRef.current);
    }

    if (shownRef.current.length >= text.length || frameRef.current !== null) {
      return;
    }

    lastTickRef.current = performance.now();

    const tick = () => {
      const now = performance.now();
      const dt = now - lastTickRef.current;
      lastTickRef.current = now;

      const remaining = targetRef.current.length - shownRef.current.length;
      const add = Math.min(
        remaining,
        REVEAL_MAX_CHARS_PER_FRAME,
        Math.max(1, Math.ceil((remaining * dt) / REVEAL_DRAIN_MS)),
      );
      shownRef.current = targetRef.current.slice(0, shownRef.current.length + add);
      setDisplayed(shownRef.current);

      frameRef.current =
        shownRef.current.length < targetRef.current.length ? requestAnimationFrame(tick) : null;
    };

    frameRef.current = requestAnimationFrame(tick);
  }, [text, isRunning]);

  useEffect(
    () => () => {
      if (frameRef.current !== null && typeof window !== "undefined") {
        cancelAnimationFrame(frameRef.current);
      }
    },
    [],
  );

  return displayed;
}
