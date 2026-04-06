"""Minimal helper for one chat turn via SSE. No timeout — waits for done event."""
import sys, json, time, httpx

BASE_URL = "http://127.0.0.1:18900"

def chat_turn(message: str, conversation_id: str, mode: str = "agent",
              client_id: str = ""):
    url = f"{BASE_URL}/api/chat"
    body = {"message": message, "conversation_id": conversation_id, "mode": mode}
    if client_id:
        body["client_id"] = client_id

    full_text = ""
    thinking_text = ""
    tool_calls = []
    events = []
    current_tool = None
    t0 = time.time()
    first_text_at = None
    ask_user_data = None

    try:
        with httpx.Client() as c:
            with c.stream("POST", url, json=body, timeout=None) as r:
                if r.status_code != 200:
                    print(json.dumps({"error": f"HTTP {r.status_code}", "body": r.text}, ensure_ascii=False))
                    return
                for line in r.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line[6:]
                    if raw.strip() == "[DONE]":
                        break
                    try:
                        evt = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    etype = evt.get("type", "")
                    events.append(etype)

                    if etype == "text_delta":
                        full_text += evt.get("content", "")
                        if first_text_at is None:
                            first_text_at = time.time()
                    elif etype == "chain_text":
                        full_text += evt.get("content", "")
                    elif etype == "thinking_delta":
                        thinking_text += evt.get("content", "")
                    elif etype == "tool_call_start":
                        tname = evt.get("name", "") or evt.get("tool_name", "")
                        tool_field = evt.get("tool")
                        if isinstance(tool_field, dict):
                            tname = tname or tool_field.get("name", "")
                        raw_args = evt.get("args") or evt.get("input") or {}
                        current_tool = {"name": tname, "args": raw_args if isinstance(raw_args, dict) else str(raw_args)[:200]}
                    elif etype == "tool_call_end":
                        if current_tool:
                            current_tool["result_preview"] = evt.get("result", "")[:500] if evt.get("result") else ""
                            tool_calls.append(current_tool)
                            current_tool = None
                    elif etype == "ask_user":
                        ask_user_data = {
                            "question": evt.get("question", ""),
                            "options": evt.get("options", []),
                            "questions": evt.get("questions", []),
                        }
                    elif etype == "error":
                        full_text += f"\n[ERROR: {evt.get('message', '')}]"
                    elif etype == "done":
                        break
    except httpx.ReadError as e:
        if full_text:
            pass
        else:
            print(json.dumps({"error": f"ReadError: {e}"}, ensure_ascii=False))
            return
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return

    elapsed = time.time() - t0
    ttfb = (first_text_at - t0) if first_text_at else elapsed

    result = {
        "conversation_id": conversation_id,
        "response": full_text[:5000],
        "response_length": len(full_text),
        "thinking_length": len(thinking_text),
        "tool_calls": tool_calls,
        "tool_count": len(tool_calls),
        "event_types": sorted(set(events)),
        "event_count": len(events),
        "ttfb_seconds": round(ttfb, 2),
        "total_seconds": round(elapsed, 2),
    }
    if ask_user_data:
        result["ask_user"] = ask_user_data
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--msg", required=True)
    p.add_argument("--conv", required=True)
    p.add_argument("--mode", default="agent")
    p.add_argument("--client-id", default="")
    args = p.parse_args()
    chat_turn(args.msg, args.conv, args.mode, args.client_id)
