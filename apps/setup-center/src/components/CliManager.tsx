import { useEffect, useState } from "react";
import { invoke } from "../platform";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Loader2, Terminal, Trash2 } from "lucide-react";

export function CliManager() {
  const [cliStatus, setCliStatus] = useState<{
    registeredCommands: string[];
    inPath: boolean;
    binDir: string;
  } | null>(null);
  const [cliLoading, setCliLoading] = useState(false);
  const [cliMsg, setCliMsg] = useState("");
  const [cliRegOpenakita, setCliRegOpenakita] = useState(true);
  const [cliRegOa, setCliRegOa] = useState(true);
  const [cliRegPath, setCliRegPath] = useState(true);

  useEffect(() => {
    loadCliStatus();
  }, []);

  async function loadCliStatus() {
    try {
      const status = await invoke<{ registeredCommands: string[]; inPath: boolean; binDir: string }>("get_cli_status");
      setCliStatus(status);
      setCliRegOpenakita(status.registeredCommands.includes("openakita"));
      setCliRegOa(status.registeredCommands.includes("oa"));
      setCliRegPath(status.inPath);
    } catch (e) {
      setCliMsg(`查询 CLI 状态失败: ${String(e)}`);
    }
  }

  async function doRegister() {
    const cmds: string[] = [];
    if (cliRegOpenakita) cmds.push("openakita");
    if (cliRegOa) cmds.push("oa");
    if (cmds.length === 0) {
      setCliMsg("请至少选择一个命令名称");
      return;
    }
    setCliLoading(true);
    setCliMsg("");
    try {
      const result = await invoke<string>("register_cli", { commands: cmds, addToPath: cliRegPath });
      setCliMsg(`✓ ${result}`);
      await loadCliStatus();
    } catch (e) {
      setCliMsg(`✗ 注册失败: ${String(e)}`);
    } finally {
      setCliLoading(false);
    }
  }

  async function doUnregister() {
    setCliLoading(true);
    setCliMsg("");
    try {
      const result = await invoke<string>("unregister_cli");
      setCliMsg(`✓ ${result}`);
      await loadCliStatus();
    } catch (e) {
      setCliMsg(`✗ 注销失败: ${String(e)}`);
    } finally {
      setCliLoading(false);
    }
  }

  const hasRegistered = cliStatus && cliStatus.registeredCommands.length > 0;

  return (
    <div className="space-y-4">
      {cliStatus && hasRegistered && (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 dark:border-emerald-500/30 dark:bg-emerald-950/20 px-4 py-3 space-y-1.5">
          <p className="text-[13px] font-semibold">已注册命令</p>
          <div className="flex items-center gap-2 flex-wrap">
            {cliStatus.registeredCommands.map(cmd => (
              <Badge key={cmd} variant="secondary" className="font-mono text-xs">{cmd}</Badge>
            ))}
            {cliStatus.inPath ? (
              <span className="text-xs text-emerald-600 dark:text-emerald-400">已在 PATH 中</span>
            ) : (
              <span className="text-xs text-amber-600 dark:text-amber-400">未在 PATH 中</span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">目录: {cliStatus.binDir}</p>
        </div>
      )}

      <div className="flex items-center gap-6 flex-wrap">
        <div className="flex items-center gap-2">
          <Checkbox id="cli-openakita" checked={cliRegOpenakita} onCheckedChange={() => setCliRegOpenakita(!cliRegOpenakita)} />
          <Label htmlFor="cli-openakita" className="text-[13px] cursor-pointer font-normal">
            <strong className="font-semibold">openakita</strong> — 完整命令
          </Label>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox id="cli-oa" checked={cliRegOa} onCheckedChange={() => setCliRegOa(!cliRegOa)} />
          <Label htmlFor="cli-oa" className="text-[13px] cursor-pointer font-normal">
            <strong className="font-semibold">oa</strong> — 简短别名
          </Label>
        </div>
        <div className="flex items-center gap-2">
          <Checkbox id="cli-path" checked={cliRegPath} onCheckedChange={() => setCliRegPath(!cliRegPath)} />
          <Label htmlFor="cli-path" className="text-[13px] cursor-pointer font-normal">添加到系统 PATH</Label>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button size="sm" onClick={doRegister} disabled={cliLoading}>
          {cliLoading ? <Loader2 className="size-3.5 animate-spin" /> : <Terminal className="size-3.5" />}
          {hasRegistered ? "更新注册" : "注册"}
        </Button>
        {hasRegistered && (
          <Button variant="outline" size="sm" onClick={doUnregister} disabled={cliLoading}>
            <Trash2 className="size-3.5" />
            注销全部
          </Button>
        )}
      </div>

      {cliMsg && (
        <div className={`rounded-md px-3 py-2 text-xs ${
          cliMsg.startsWith("✓")
            ? "bg-emerald-50 text-emerald-600 border border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-400 dark:border-emerald-500/30"
            : cliMsg.startsWith("✗")
              ? "bg-red-50 text-red-600 border border-red-200 dark:bg-red-950/30 dark:text-red-400 dark:border-red-500/30"
              : "bg-amber-50 text-amber-600 border border-amber-200 dark:bg-amber-950/30 dark:text-amber-400 dark:border-amber-500/30"
        }`}>
          {cliMsg}
        </div>
      )}
    </div>
  );
}
