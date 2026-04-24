import React, { useEffect, useState, useCallback } from "react";
import { invoke } from "../platform";
import { onWsEvent } from "../platform/websocket";
import logoUrl from "../assets/logo.png";
import "../styles/pet.css";

type PetState = "idle" | "thinking" | "tool_execution" | "completed" | "error";

export const PetView: React.FC = () => {
  const [petState, setPetState] = useState<PetState>("idle");
  const [toolName, setToolName] = useState("");

  // Force transparent background for pet window
  useEffect(() => {
    document.getElementById("boot")?.remove();
    const style = document.createElement("style");
    style.id = "pet-bg-fix";
    style.textContent = "html,body,#root{background:transparent!important;background-color:transparent!important;background-image:none!important;margin:0!important;overflow:hidden!important}";
    document.head.appendChild(style);
    document.documentElement.style.setProperty("background", "transparent", "important");
    document.body.style.setProperty("background", "transparent", "important");
    document.body.style.setProperty("background-color", "transparent", "important");
    document.body.style.setProperty("background-image", "none", "important");
    return () => { document.getElementById("pet-bg-fix")?.remove(); };
  }, []);

  useEffect(() => {
    const unsub = onWsEvent((event, data: any) => {
      if (event === "pet-status-update") {
        let s = data?.status as string;
        if (s === "success") s = "completed";
        setPetState(s as PetState);
        if (s === "completed" || s === "error") {
          setTimeout(() => setPetState("idle"), 2000);
        }
        if (s === "tool_execution" && data?.tool_name) {
          setToolName(data.tool_name);
        }
      }
    });
    return () => unsub();
  }, []);

  const handleDrag = useCallback((e: React.MouseEvent) => {
    if (e.buttons === 1) invoke("start_dragging").catch(() => {});
  }, []);

  let statusText = "空闲打盹";
  if (petState === "thinking") statusText = "思考中...";
  else if (petState === "tool_execution") statusText = `执行: ${toolName}`;
  else if (petState === "completed") statusText = "完成！";
  else if (petState === "error") statusText = "出错了";

  return (
    <div className="pet-root" onMouseDown={handleDrag} data-tauri-drag-region>
      <div className="pet-shadow-wrap">
        <div className={`pet-shadow pet-shadow-${petState}`} />
      </div>
      <img
        src={logoUrl}
        alt="OpenAkita"
        className={`pet-img pet-anim-${petState}`}
        draggable={false}
        data-tauri-drag-region
      />
      <div className={`pet-label pet-label-${petState}`}>{statusText}</div>
    </div>
  );
};

export default PetView;

