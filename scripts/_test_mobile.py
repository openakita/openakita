"""Helper for mobile testing via CDP + ADB screenshots."""
import json, sys, asyncio, subprocess, os
import websockets

WS_URL = None

def get_ws_url():
    global WS_URL
    if WS_URL:
        return WS_URL
    out = subprocess.check_output(["curl", "-s", "http://localhost:9222/json"], text=True)
    data = json.loads(out)
    WS_URL = data[0]["webSocketDebuggerUrl"]
    return WS_URL

async def cdp_eval(js: str):
    uri = get_ws_url()
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": js, "returnByValue": True},
        }))
        resp = json.loads(await ws.recv())
        result = resp.get("result", {}).get("result", {})
        return result.get("value", result.get("description", ""))

def screenshot(name: str):
    path = f"d:/coder/myagent/screenshots/{name}.png"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["adb", "exec-out", "screencap", "-p"],
                   stdout=open(path, "wb"), check=True)
    print(f"Screenshot saved: {path}")
    return path

def run_js(js: str):
    return asyncio.run(cdp_eval(js))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(run_js(sys.argv[1]))
