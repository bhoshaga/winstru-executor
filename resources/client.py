#!/usr/bin/env python3
"""
client_lite.py - Minimal Nova Bridge Windows Executor
A barebones client for testing Nova Bridge message forwarding.
Only connects, executes code, and returns results.
"""

import asyncio
import websockets
import json
# subprocess import removed - using asyncio.subprocess instead
import sys
import uuid
from datetime import datetime
import time
try:
    from sap_class import SAP2000Manager
    SAP_AVAILABLE = True
except ImportError as e:
    print(f"[WARNING] SAP2000 support not available: {e}")
    SAP_AVAILABLE = False

# Configuration
EXECUTOR_ID = f"windows_lite_{uuid.uuid4().hex[:8]}"  # Must start with "windows_" to be recognized as executor
NOVA_WS_URL = "wss://api.stru.ai"
MAX_TIMEOUT = 300  # seconds for code execution (5 minutes)
HEARTBEAT_TIMEOUT = 60  # seconds to wait for ping from server

# Initialize SAP2000 manager if available
if SAP_AVAILABLE:
    sap_manager = SAP2000Manager()
    print(f"[INFO] SAP2000 support initialized")
else:
    sap_manager = None
    print(f"[WARNING] SAP2000 support disabled (missing dependencies)")


async def execute_code(code, timeout=MAX_TIMEOUT):
    """Execute Python code using async subprocess for non-blocking execution."""
    try:
        # Create async subprocess
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Wait for completion with timeout
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            # Decode bytes to string
            stdout_str = stdout.decode('utf-8') if stdout else ''
            stderr_str = stderr.decode('utf-8') if stderr else ''

            return {
                "status": "completed" if process.returncode == 0 else "failed",
                "stdout": stdout_str,
                "stderr": stderr_str,
                "return_code": process.returncode
            }

        except asyncio.TimeoutError:
            # Kill the process if it times out
            process.kill()
            await process.wait()  # Clean up the process
            return {
                "status": "timeout",
                "error": f"Execution timeout ({timeout}s)"
            }

    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


async def handle_job_execution(websocket, job_id, code):
    """Execute a job asynchronously and send result back."""
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

async def handle_message(websocket, message):
    """Handle incoming message and send response if needed."""
    msg_type = message.get("type")

    if msg_type == "execute":
        job_id = message.get("job_id")
        code = message.get("code", "")

        # Spawn job execution as a separate task (non-blocking)
        asyncio.create_task(handle_job_execution(websocket, job_id, code))

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

    # SAP2000 message handlers
    elif msg_type == "sap_connect":
        job_id = message.get("job_id")
        timeout = message.get("timeout", 30)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 connect request (job {job_id})")
        result = await sap_manager.connect(timeout)

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))

        # Log more details on error
        if result['status'] == 'error':
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 connect ERROR: {result.get('message', 'Unknown error')}")
            if 'traceback' in result:
                print(f"Traceback snippet: {result['traceback'][:300]}")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 connect: {result['status']}")

    elif msg_type == "sap_execute":
        job_id = message.get("job_id")
        code = message.get("code", "")
        timeout = message.get("timeout", 60)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 execute (job {job_id}): {code[:50]}...")
        result = await sap_manager.execute(code, timeout)

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 execute: {result['status']}")

    elif msg_type == "sap_status":
        job_id = message.get("job_id")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 status request (job {job_id})")
        result = await sap_manager.get_status()

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 status sent")

    elif msg_type == "sap_disconnect":
        job_id = message.get("job_id")
        save = message.get("save", False)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 disconnect request (job {job_id})")
        result = await sap_manager.disconnect(save)

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 disconnect: {result['status']}")

    elif msg_type == "sap_open_file":
        job_id = message.get("job_id")
        file_path = message.get("file_path")

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 open file (job {job_id}): {file_path}")
        result = await sap_manager.open_file(file_path)

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 open file: {result['status']}")

    elif msg_type == "sap_save_file":
        job_id = message.get("job_id")
        file_path = message.get("file_path", None)

        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 save file (job {job_id})")
        result = await sap_manager.save_file(file_path)

        response = {
            "type": "result",
            "job_id": job_id,
            "executor_id": EXECUTOR_ID,
            "result": result  # Keep SAP response as-is
        }
        await websocket.send(json.dumps(response))
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SAP2000 save file: {result['status']}")

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