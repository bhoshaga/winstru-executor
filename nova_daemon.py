#!/usr/bin/env python3
"""
nova_daemon.py - Background daemon with proper concurrency support
"""

import asyncio
import websockets
import json
import sys
import os
import uuid
import time
import socket
from datetime import datetime
from typing import Dict

# Configuration
DAEMON_HOST = '127.0.0.1'
IDLE_TIMEOUT = 60  # seconds
NOVA_WS_URL = "wss://api.stru.ai"
PORT_FILE = os.path.expanduser('~/.nova-daemon-port.txt')

class NovaDaemon:
    def __init__(self, port):
        self.port = port
        self.websocket = None
        self.mac_id = None
        self.disconnect_timestamp = time.time() + IDLE_TIMEOUT
        self.running = True

        # Track pending requests
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.message_router_task = None

    async def ensure_connection(self):
        """Ensure WebSocket connection is active"""
        if self.websocket:
            try:
                await self.websocket.ping()
                return True
            except:
                self.websocket = None
                if self.message_router_task:
                    self.message_router_task.cancel()

        # Create new connection
        if not self.mac_id:
            self.mac_id = f"mac_{uuid.uuid4().hex[:8]}"

        uri = f"{NOVA_WS_URL}/nova/bridge/connect?client_type=mac&client_id={self.mac_id}"

        try:
            print(f"[DAEMON] Connecting to Nova Bridge...")
            self.websocket = await websockets.connect(uri, ping_interval=20)

            # Wait for welcome
            welcome = await self.websocket.recv()
            print(f"[DAEMON] Connected as {self.mac_id}")

            # Start message router
            self.message_router_task = asyncio.create_task(self.message_router())

            return True
        except Exception as e:
            print(f"[DAEMON] Connection failed: {e}")
            return False

    async def message_router(self):
        """Central message router that handles all WebSocket receives"""
        try:
            while self.websocket:
                try:
                    message = await self.websocket.recv()
                    data = json.loads(message)

                    msg_type = data.get("type")

                    # Route to appropriate handler
                    if msg_type == "executors_list":
                        # Find waiting request for executors list
                        for req_id, future in list(self.pending_requests.items()):
                            if req_id.startswith("list_executors_"):
                                if not future.done():
                                    future.set_result(data)
                                del self.pending_requests[req_id]
                                break

                    elif msg_type == "jobs_list":
                        # Find waiting request for jobs list
                        for req_id, future in list(self.pending_requests.items()):
                            if req_id.startswith("list_jobs_"):
                                if not future.done():
                                    future.set_result(data)
                                del self.pending_requests[req_id]
                                break

                    elif msg_type == "job_result":
                        # Find waiting request for job result
                        job_id = data.get("job_id")
                        for req_id, future in list(self.pending_requests.items()):
                            if req_id.startswith(f"get_result_{job_id}_"):
                                if not future.done():
                                    future.set_result(data)
                                del self.pending_requests[req_id]
                                break

                    elif msg_type in ["result", "job_timeout"]:
                        # Find waiting execute request
                        job_id = data.get("job_id")
                        if job_id:
                            for req_id, future in list(self.pending_requests.items()):
                                if req_id == f"execute_{job_id}":
                                    if not future.done():
                                        future.set_result(data)
                                    del self.pending_requests[req_id]
                                    break

                except asyncio.CancelledError:
                    break
                except websockets.exceptions.ConnectionClosed:
                    print(f"[DAEMON] WebSocket connection closed")
                    self.websocket = None
                    break
                except Exception as e:
                    print(f"[DAEMON] Error in message router: {e}")
                    # If WebSocket is broken, stop the router
                    if self.websocket:
                        try:
                            await self.websocket.ping()
                        except:
                            print(f"[DAEMON] WebSocket ping failed, stopping router")
                            self.websocket = None
                            break

        except Exception as e:
            print(f"[DAEMON] Message router crashed: {e}")

    async def handle_client(self, reader, writer):
        """Handle request from nova-lite CLI"""
        try:
            # Read request
            data = await reader.read(65536)
            request = json.loads(data.decode())

            # Check for stop command
            if request.get('command') == 'stop':
                print("[DAEMON] Received stop command")
                self.running = False
                writer.close()
                return

            request_type = request.get('type', 'execute')
            print(f"[DAEMON] Received request: type={request_type}")

            # Ensure connection
            if not await self.ensure_connection():
                response = {
                    'status': 'error',
                    'error': 'Failed to connect to Nova Bridge'
                }
                writer.write(json.dumps(response).encode())
                await writer.drain()
                writer.close()
                return

            # Generate unique request ID
            req_uuid = uuid.uuid4().hex[:8]

            # Handle different request types
            if request_type == 'list_executors':
                req_id = f"list_executors_{req_uuid}"
                future = asyncio.Future()
                self.pending_requests[req_id] = future

                msg = {"type": "list_executors"}
                await self.websocket.send(json.dumps(msg))

                # Wait for response
                try:
                    data = await asyncio.wait_for(future, timeout=5)
                    response = {
                        'status': 'success',
                        'executors': data.get('executors', [])
                    }
                except asyncio.TimeoutError:
                    response = {'status': 'error', 'error': 'Timeout waiting for response'}
                    if req_id in self.pending_requests:
                        del self.pending_requests[req_id]

            elif request_type == 'list_jobs':
                req_id = f"list_jobs_{req_uuid}"
                future = asyncio.Future()
                self.pending_requests[req_id] = future

                msg = {"type": "list_jobs"}
                await self.websocket.send(json.dumps(msg))

                # Wait for response
                try:
                    data = await asyncio.wait_for(future, timeout=5)
                    response = {
                        'status': 'success',
                        'jobs': data.get('jobs', [])
                    }
                except asyncio.TimeoutError:
                    response = {'status': 'error', 'error': 'Timeout waiting for response'}
                    if req_id in self.pending_requests:
                        del self.pending_requests[req_id]

            elif request_type == 'get_result':
                job_id = request.get('job_id')
                req_id = f"get_result_{job_id}_{req_uuid}"
                future = asyncio.Future()
                self.pending_requests[req_id] = future

                msg = {
                    "type": "get_result",
                    "job_id": job_id
                }
                await self.websocket.send(json.dumps(msg))

                # Wait for response
                try:
                    data = await asyncio.wait_for(future, timeout=5)
                    response = {
                        'status': 'success',
                        'result': data.get('result', {})
                    }
                except asyncio.TimeoutError:
                    response = {'status': 'error', 'error': 'Timeout waiting for response'}
                    if req_id in self.pending_requests:
                        del self.pending_requests[req_id]

            else:
                # Handle execute or custom message request
                job_id = request.get('job_id')
                print(f"[DAEMON] Request: type={request_type}, job_id={job_id}")

                # Calculate new disconnect timestamp
                job_timeout = request.get('timeout', 30)
                new_disconnect = time.time() + job_timeout + IDLE_TIMEOUT

                if new_disconnect > self.disconnect_timestamp:
                    self.disconnect_timestamp = new_disconnect
                    print(f"[DAEMON] Extended disconnect to {job_timeout + IDLE_TIMEOUT}s from now")

                # Check if this is a custom message request
                if request_type == 'message':
                    # Use the provided message directly
                    msg = request.get('message', {})
                    # Ensure job_id is set
                    if 'job_id' not in msg:
                        msg['job_id'] = job_id
                else:
                    # Build standard execute message
                    msg = {
                        "type": "execute",
                        "job_id": job_id,
                        "code": request['code'],
                        "timeout": job_timeout
                    }

                if request.get('executor_id'):
                    msg['executor_id'] = request['executor_id']

                req_id = f"execute_{job_id}"
                future = asyncio.Future()
                self.pending_requests[req_id] = future

                await self.websocket.send(json.dumps(msg))

                # Wait for result with timeout
                timeout = job_timeout + 5
                try:
                    data = await asyncio.wait_for(future, timeout=timeout)

                    if data.get("type") == "result":
                        response = {
                            'status': 'success',
                            'result': data
                        }
                    elif data.get("type") == "job_timeout":
                        response = {
                            'status': 'job_timeout',
                            'job_id': job_id,
                            'timeout': data.get('timeout'),
                            'message': data.get('message')
                        }
                    else:
                        response = {
                            'status': 'success',
                            'result': data
                        }

                except asyncio.TimeoutError:
                    response = {
                        'status': 'timeout',
                        'job_id': job_id,
                        'message': f"Timeout waiting for response (waited {timeout}s)"
                    }
                    if req_id in self.pending_requests:
                        del self.pending_requests[req_id]

            # Send response back to client
            writer.write(json.dumps(response).encode())
            await writer.drain()
            writer.close()

            # Reset disconnect timestamp after any successful operation
            self.disconnect_timestamp = time.time() + IDLE_TIMEOUT

        except Exception as e:
            print(f"[DAEMON] Error handling client: {e}")
            try:
                response = {'status': 'error', 'error': str(e)}
                writer.write(json.dumps(response).encode())
                await writer.drain()
                writer.close()
            except:
                pass

    async def idle_monitor(self):
        """Monitor for disconnect timeout"""
        while self.running:
            await asyncio.sleep(10)

            if time.time() > self.disconnect_timestamp:
                print(f"[DAEMON] Disconnect timeout reached, shutting down...")
                self.running = False

                # Close WebSocket
                if self.websocket:
                    await self.websocket.close()

                break

    async def run(self):
        """Main daemon loop"""
        # Create TCP server
        server = await asyncio.start_server(
            self.handle_client,
            DAEMON_HOST,
            self.port
        )

        print(f"[DAEMON] Started on {DAEMON_HOST}:{self.port}")
        print(f"[DAEMON] PID: {os.getpid()}")

        # Write port to file
        try:
            with open(PORT_FILE, 'w') as f:
                f.write(str(self.port))
            print(f"[DAEMON] Port written to {PORT_FILE}")
        except Exception as e:
            print(f"[DAEMON] Warning: Could not write port file: {e}")

        # Start idle monitor
        monitor_task = asyncio.create_task(self.idle_monitor())

        # Run until idle timeout
        async with server:
            while self.running:
                await asyncio.sleep(1)

        print("[DAEMON] Shutting down...")

        # Cleanup
        monitor_task.cancel()

        if self.message_router_task:
            self.message_router_task.cancel()

        if self.websocket:
            await self.websocket.close()

        # Remove port file
        try:
            if os.path.exists(PORT_FILE):
                os.remove(PORT_FILE)
                print(f"[DAEMON] Port file removed")
        except Exception as e:
            print(f"[DAEMON] Warning: Could not remove port file: {e}")


async def main():
    # Get port from nova-lite
    port = int(sys.argv[1])
    daemon = NovaDaemon(port)
    await daemon.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[DAEMON] Interrupted")