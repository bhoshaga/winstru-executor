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
import time

# Configuration
EXECUTOR_ID = f"windows_lite_{uuid.uuid4().hex[:8]}"  # Must start with "windows_" to be recognized as executor
NOVA_WS_URL = "wss://api.stru.ai"
MAX_TIMEOUT = 30  # seconds for code execution
HEARTBEAT_TIMEOUT = 60  # seconds to wait for ping from server


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
        # Respond to application-level ping with latency calculation
        timestamp = message.get("timestamp")
        pong_msg = {
            "type": "pong",
            "executor_id": EXECUTOR_ID
        }

        if timestamp:
            # Include original timestamp for latency calculation
            pong_msg["timestamp"] = timestamp
            try:
                from datetime import timezone
                sent_time = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)
                current_time = datetime.now(timezone.utc)
                latency_ms = (current_time - sent_time).total_seconds() * 1000
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Received ping, latency: {latency_ms:.0f}ms")
            except:
                pass

        await websocket.send(json.dumps(pong_msg))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sent pong response")

    elif msg_type == "connected":
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message.get('message', 'Connected')}")

    else:
        # Ignore other message types
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Ignored message type: {msg_type}")


async def monitor_heartbeat(last_ping_time, websocket):
    """Monitor heartbeat and close connection if no ping received."""
    while True:
        await asyncio.sleep(10)  # Check every 10 seconds
        if time.time() - last_ping_time['time'] > HEARTBEAT_TIMEOUT:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] No ping received for {HEARTBEAT_TIMEOUT}s, disconnecting...")
            await websocket.close()
            break


async def main():
    """Main connection loop with auto-reconnect and exponential backoff."""
    uri = f"{NOVA_WS_URL}/nova/bridge/connect?client_type=windows&client_id={EXECUTOR_ID}"

    print(f"Nova Bridge Lite Client")
    print(f"Executor ID: {EXECUTOR_ID}")
    print(f"Connecting to: {NOVA_WS_URL}")
    print("-" * 50)

    reconnect_delay = 1  # Start with 1 second
    max_delay = 30  # Max 30 seconds

    while True:  # Reconnection loop
        try:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connecting to Nova Bridge...")

            async with websockets.connect(
                uri,
                ping_interval=20,  # Send WebSocket ping every 20 seconds
                ping_timeout=10    # Wait 10 seconds for WebSocket pong
            ) as websocket:

                print(f"[{datetime.now().strftime('%H:%M:%S')}] Connected successfully!")

                # Reset reconnect delay on successful connection
                reconnect_delay = 1

                # Track last ping time
                last_ping_time = {'time': time.time()}

                # Start heartbeat monitor
                monitor_task = asyncio.create_task(monitor_heartbeat(last_ping_time, websocket))

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

                        # Update last ping time
                        if data.get('type') == 'ping':
                            last_ping_time['time'] = time.time()

                        await handle_message(websocket, data)
                    except json.JSONDecodeError:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Invalid JSON received")
                    except Exception as e:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Error handling message: {e}")

                # Cancel monitor task when connection closes
                monitor_task.cancel()

        except websockets.exceptions.ConnectionClosed:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection closed")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Connection error: {e}")

        # Exponential backoff for reconnection
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Reconnecting in {reconnect_delay} seconds...")
        await asyncio.sleep(reconnect_delay)

        # Increase delay with exponential backoff
        reconnect_delay = min(reconnect_delay * 2, max_delay)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Shutting down...")