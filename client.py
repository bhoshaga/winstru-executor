#!/usr/bin/env python3
"""
client_lite.py - Minimal Nova Bridge Windows Executor
A barebones client for testing Nova Bridge message forwarding.
Only connects, executes code, and returns results.
"""

import asyncio
import websockets
import json
import subprocess
import sys
import uuid
from datetime import datetime

# Configuration
EXECUTOR_ID = f"windows_lite_{uuid.uuid4().hex[:8]}"  # Must start with "windows_" to be recognized as executor
NOVA_WS_URL = "wss://api.stru.ai"
MAX_TIMEOUT = 30  # seconds for code execution


async def execute_code(code, timeout=MAX_TIMEOUT):
    """Execute Python code using subprocess and return results."""
    try:
        # Run code in subprocess
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        return {
            "status": "completed" if result.returncode == 0 else "failed",
            "stdout": result.stdout,
            "stderr": result.stderr,
            "return_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "error": f"Execution timeout ({timeout}s)"
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def handle_message(websocket, message):
    """Handle incoming message and send response if needed."""
    msg_type = message.get("type")

    if msg_type == "execute":
        job_id = message.get("job_id")
        code = message.get("code", "")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] Executing job {job_id}")

        # Execute the code
        result = await execute_code(code)

        # Send result back
        response = {
            "type": "result",
            "job_id": job_id,
            "status": result["status"],
            "executor_id": EXECUTOR_ID,
            "result": result
        }

        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Job {job_id} {result['status']}")

    elif msg_type == "ping":
        # Optional: respond to application-level ping
        await websocket.send(json.dumps({
            "type": "pong",
            "executor_id": EXECUTOR_ID
        }))

    elif msg_type == "connected":
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message.get('message', 'Connected')}")

    else:
        # Ignore other message types
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Ignored message type: {msg_type}")


async def main():
    """Main connection loop with auto-reconnect."""
    uri = f"{NOVA_WS_URL}/nova/bridge/connect?client_type=windows&client_id={EXECUTOR_ID}"

    print(f"Nova Bridge Lite Client")
    print(f"Executor ID: {EXECUTOR_ID}")
    print(f"Connecting to: {NOVA_WS_URL}")
    print("-" * 50)

    while True:  # Reconnection loop
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connecting to Nova Bridge...")

            async with websockets.connect(
                uri,
                ping_interval=20,  # Send ping every 20 seconds
                ping_timeout=10    # Wait 10 seconds for pong
            ) as websocket:

                print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected successfully!")

                # Send ready status
                ready_msg = {
                    "type": "status",
                    "status": "ready",
                    "executor_id": EXECUTOR_ID,
                    "capabilities": {
                        "python_version": sys.version.split()[0],
                        "platform": sys.platform
                    }
                }
                await websocket.send(json.dumps(ready_msg))

                # Message handling loop
                async for message in websocket:
                    try:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] RAW MESSAGE: {message[:200]}")  # Debug
                        data = json.loads(message)
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Received: type={data.get('type')}, job_id={data.get('job_id', 'N/A')}")  # Debug
                        await handle_message(websocket, data)
                    except json.JSONDecodeError:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Invalid JSON received")
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error handling message: {e}")

        except websockets.exceptions.ConnectionClosed:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection closed")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection error: {e}")

        # Wait before reconnecting
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Reconnecting in 5 seconds...")
        await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Shutting down...")