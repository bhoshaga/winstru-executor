# -*- coding: utf-8 -*-
import asyncio
import json
import base64
import subprocess
import tempfile
import os
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from contextlib import asynccontextmanager
import sys
import traceback
import signal
import getpass
try:
    import psutil  # Add this for Mathcad status checking
except ImportError:
    psutil = None  # Will run without process monitoring on Mac

# Fix SSL certificate issues on Windows
import ssl
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()




from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import websockets
import httpx
import aiofiles




# Check Python version for asyncio.to_thread support
if sys.version_info >= (3, 9):
 # Python 3.9+ has asyncio.to_thread
 to_thread = asyncio.to_thread
else:
 # Fallback for older Python versions - return coroutine directly
 def to_thread(func, *args, **kwargs):
     loop = asyncio.get_event_loop()
     return loop.run_in_executor(None, func, *args, **kwargs)




# Configure logging
logging.basicConfig(
 level=logging.INFO,
 format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
 handlers=[
     logging.StreamHandler(),
     logging.FileHandler('nova_executor.log')
 ]
)
logger = logging.getLogger(__name__)




# Configuration
EXECUTOR_ID = os.getenv("NOVA_EXECUTOR_ID", f"windows_{uuid.uuid4().hex[:8]}")
NOVA_API_URL = os.getenv("NOVA_API_URL", "https://api.stru.ai")
NOVA_WS_URL = NOVA_API_URL.replace("https://", "wss://").replace("http://", "ws://")
MAX_EXECUTION_TIME = int(os.getenv("NOVA_MAX_EXECUTION_TIME", "60"))




# Get current username for working directory
current_user = getpass.getuser()
MATHCAD_WORKING_DIR = os.getenv("NOVA_MATHCAD_WORKING_DIR",
                              f"C:\\Users\\{current_user}\\mathcadstuff\\working")




# Global state
executor_state = {
 "connected": False,
 "websocket": None,
 "current_jobs": {},
 "completed_jobs": {},
 "stats": {
     "total_jobs": 0,
     "successful_jobs": 0,
     "failed_jobs": 0,
     "start_time": datetime.now(timezone.utc)
 }
}




# Thread pool for expensive operations (like open_files)
from concurrent.futures import ThreadPoolExecutor
POOL_SIZE = 6  # Reasonable for Windows workstation
_io_executor = ThreadPoolExecutor(max_workers=POOL_SIZE, thread_name_prefix="nova_io")
_io_semaphore = asyncio.Semaphore(POOL_SIZE * 2)  # Limit concurrent operations




# Mathcad status cache
mathcad_status_cache = {
 "status": None,
 "timestamp": None,
 "cache_duration": 30,  # seconds
}
mathcad_status_lock = asyncio.Lock()  # Proper lock for cache updates




# SAP2000 state
sap2000_state = {
 "instance": None,      # SAP2000 COM object
 "model": None,         # SapModel object
 "is_connected": False,
 "model_path": None,
 "last_activity": None
}








class ExecuteRequest(BaseModel):
 code: str
 files: Optional[List[Dict]] = []
 timeout: Optional[int] = MAX_EXECUTION_TIME








class JobStatus(BaseModel):
 job_id: str
 status: str
 result: Optional[Dict] = None
 started_at: Optional[datetime] = None
 completed_at: Optional[datetime] = None








class ExecutorInfo(BaseModel):
 executor_id: str
 status: str
 connected: bool
 stats: Dict
 current_jobs: int
 system_info: Dict








async def connect_to_nova_bridge():
    """Maintain persistent WebSocket connection to Nova Bridge"""
    uri = f"{NOVA_WS_URL}/nova/bridge/connect?client_type=windows&client_id={EXECUTOR_ID}"
    
    # Create SSL context that uses certifi certificates
    ssl_context = ssl.create_default_context()
    ssl_context.load_verify_locations(certifi.where())
    
    try:
        while True:
            try:
                logger.info(f"Connecting to Nova Bridge at {uri}")




                async with websockets.connect(
                        uri,
                        ssl=ssl_context,
                        ping_interval=20,  # Send ping every 20 seconds
                        ping_timeout=10,  # Wait 10 seconds for pong
                        close_timeout=10  # Wait 10 seconds for close
                ) as websocket:
                    executor_state["connected"] = True
                    executor_state["websocket"] = websocket
                    
                    # Create a lock for this websocket connection to prevent concurrent sends
                    ws_lock = asyncio.Lock()




                    # Receive connection confirmation
                    welcome = await websocket.recv()
                    logger.info(f"Connected to Nova Bridge: {welcome}")




                    # Send ready status
                    ready_msg = {
                        "type": "status",
                        "status": "ready",
                        "executor_id": EXECUTOR_ID,
                        "capabilities": {
                            "max_execution_time": MAX_EXECUTION_TIME,
                            "python_version": sys.version,
                            "platform": sys.platform
                        }
                    }
                    async with ws_lock:
                        await websocket.send(json.dumps(ready_msg))
                    logger.info(f"Sent ready status: {ready_msg['status']}")




                    # Listen for execution requests
                    while True:
                        try:
                            message = await websocket.recv()
                            logger.debug(f"Received raw message: {message[:500]}...")  # Log first 500 chars
                            data = json.loads(message)
                            logger.info(f"Received message type: {data.get('type')} for job: {data.get('job_id', 'N/A')}")




                            if data["type"] == "execute":
                                logger.info(f"Processing execution request for job {data['job_id']}")
                                logger.debug(f"Code length: {len(data.get('code', ''))} chars")
                                logger.debug(f"Files included: {[f['name'] for f in data.get('files', [])]}")
                                # Handle execution in background
                                asyncio.create_task(execute_job(websocket, data, ws_lock))
                            elif data["type"] == "ping":
                                logger.info("Received ping, sending pong - connection alive")
                                async with ws_lock:
                                    await websocket.send(json.dumps({
                                        "type": "pong",
                                        "executor_id": EXECUTOR_ID
                                    }))
                            elif data["type"] == "mathcad_status":
                                logger.info("Received Mathcad status request")
                                force_refresh = data.get("force_refresh", False)
                                # Handle status request asynchronously
                                asyncio.create_task(handle_mathcad_status_request(websocket, force_refresh, ws_lock))
                            elif data["type"] == "shutdown":
                                logger.warning("Received shutdown command from Mac")
                                async with ws_lock:
                                    await websocket.send(json.dumps({
                                        "type": "shutdown_acknowledged",
                                        "executor_id": EXECUTOR_ID,
                                        "message": "Shutting down Nova executor"
                                    }))
                                # Close websocket and trigger shutdown
                                await websocket.close()
                                logger.info("Initiating graceful shutdown...")
                                # Use sys.exit for clean shutdown on Windows
                                sys.exit(0)




                        except websockets.exceptions.ConnectionClosed:
                            logger.warning("WebSocket connection closed")
                            break
                        except Exception as e:
                            logger.error(f"Error handling message: {e}")




            except Exception as e:
                logger.error(f"Connection error: {e}")
            finally:
                # Always clear the connection state when websocket closes
                executor_state["connected"] = False
                executor_state["websocket"] = None
                logger.info("WebSocket connection cleaned up")




            # Wait before reconnecting
            await asyncio.sleep(5)
            logger.info("Reconnecting to Nova Bridge...")
    except asyncio.CancelledError:
        logger.info("WebSocket connection task cancelled - shutting down gracefully")
        return








async def execute_job(websocket, job_data, ws_lock):
 """Execute Python code using asyncio.to_thread to avoid blocking event loop"""
 job_id = job_data["job_id"]
 code = job_data["code"]
 timeout = job_data.get("timeout", MAX_EXECUTION_TIME)




 logger.info(f"Executing job {job_id}")
 logger.debug(f"Code length: {len(code)} chars, Timeout: {timeout}s")
 logger.debug(f"Code preview (first 200 chars): {code[:200]}...")




 # Track job
 executor_state["current_jobs"][job_id] = {
     "status": "running",
     "started_at": datetime.now(timezone.utc)
 }
 executor_state["stats"]["total_jobs"] += 1




 # Send status update immediately
 async with ws_lock:
     await websocket.send(json.dumps({
         "type": "status",
         "job_id": job_id,
         "status": "running",
         "executor_id": EXECUTOR_ID
     }))




 try:
     logger.info(f"Executing Python code for job {job_id}")
     start_time = datetime.now(timezone.utc)




     try:
         # Determine execution method based on code length
         if len(code) > 8000 or (sys.platform == 'win32' and len(code) > 2000):
             # Use temp file for long code
             script_path = f'_nova_temp_{uuid.uuid4().hex[:16]}.py'
             with open(script_path, 'w', encoding='utf-8') as f:
                 f.write(code)
             logger.debug(f"Code too long for -c, using temp script: {script_path}")




             try:
                 # Execute in thread to avoid blocking
                 result = await to_thread(
                     subprocess.run,
                     [sys.executable, script_path],
                     capture_output=True,
                     text=True,
                     encoding='utf-8',
                     errors='replace',
                     timeout=timeout
                 )
             finally:
                 # Clean up temp script file
                 try:
                     os.remove(script_path)
                 except:
                     pass
         else:
             # Execute code directly with -c
             result = await to_thread(
                 subprocess.run,
                 [sys.executable, "-c", code],
                 capture_output=True,
                 text=True,
                 encoding='utf-8',
                 errors='replace',
                 timeout=timeout
             )




         execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()




         logger.info(f"Execution completed in {execution_time:.2f}s with return code: {result.returncode}")
         if result.stdout:
             logger.debug(f"STDOUT (first 500 chars): {result.stdout[:500]}...")
         if result.stderr:
             logger.warning(f"STDERR (first 500 chars): {result.stderr[:500]}...")




         # Prepare response
         response = {
             "type": "result",
             "job_id": job_id,
             "status": "completed" if result.returncode == 0 else "failed",
             "executor_id": EXECUTOR_ID,
             "result": {
                 "stdout": result.stdout,
                 "stderr": result.stderr,
                 "return_code": result.returncode,
                 "execution_time": execution_time
             }
         }




         # Update stats
         if result.returncode == 0:
             executor_state["stats"]["successful_jobs"] += 1
         else:
             executor_state["stats"]["failed_jobs"] += 1




     except subprocess.TimeoutExpired:
         logger.warning(f"Job {job_id} timed out after {timeout}s")
         response = {
             "type": "result",
             "job_id": job_id,
             "status": "timeout",
             "executor_id": EXECUTOR_ID,
             "result": {
                 "error": f"Execution timeout exceeded ({timeout}s)",
                 "execution_time": timeout
             }
         }
         executor_state["stats"]["failed_jobs"] += 1




     except Exception as e:
         logger.error(f"Execution error for job {job_id}: {e}")
         response = {
             "type": "result",
             "job_id": job_id,
             "status": "error",
             "executor_id": EXECUTOR_ID,
             "result": {
                 "error": str(e),
                 "traceback": traceback.format_exc()
             }
         }
         executor_state["stats"]["failed_jobs"] += 1




 except Exception as e:
     logger.error(f"Unexpected error for job {job_id}: {e}")
     response = {
         "type": "result",
         "job_id": job_id,
         "status": "error",
         "executor_id": EXECUTOR_ID,
         "result": {
             "error": str(e),
             "traceback": traceback.format_exc()
         }
     }
     executor_state["stats"]["failed_jobs"] += 1




 # ALWAYS send result back to Mac
 try:
     async with ws_lock:
         await websocket.send(json.dumps(response))
     logger.info(f"Result sent back to Mac for job {job_id}")
     logger.debug(f"Result sent: {json.dumps(response)[:500]}...")
 except Exception as send_error:
     logger.error(f"Failed to send result back to Mac for job {job_id}: {send_error}")




 # Update job tracking
 if job_id in executor_state["current_jobs"]:
     executor_state["current_jobs"][job_id].update({
         "status": response["status"],
         "completed_at": datetime.now(timezone.utc),
         "result": response.get("result", {})
     })




     # Move to completed jobs
     executor_state["completed_jobs"][job_id] = executor_state["current_jobs"].pop(job_id)




     # Keep only last 100 completed jobs
     if len(executor_state["completed_jobs"]) > 100:
         oldest_job = min(executor_state["completed_jobs"].keys(),
                          key=lambda k: executor_state["completed_jobs"][k]["completed_at"])
         del executor_state["completed_jobs"][oldest_job]




 logger.info(f"Job {job_id} completed with status: {response['status']}")




 # Update Mathcad status cache after job execution (non-blocking)
 asyncio.create_task(update_mathcad_status_cache())








@asynccontextmanager
async def lifespan(app: FastAPI):
 """Application lifespan manager"""
 logger.info(f"Starting Nova Executor: {EXECUTOR_ID}")
 logger.info(f"Mathcad working directory: {MATHCAD_WORKING_DIR}")




 # Start WebSocket connection in background
 asyncio.create_task(connect_to_nova_bridge())




 # Start background Mathcad status monitoring (every 2 minutes)
 asyncio.create_task(background_mathcad_monitor())




 yield




 logger.info("Shutting down Nova Executor")
 # Clean up thread pool - wait for running tasks to complete
 logger.info("Shutting down IO thread pool...")
 _io_executor.shutdown(wait=True, cancel_futures=True)








# Create FastAPI app
app = FastAPI(
 title="Nova Windows Executor",
 description="Windows executor for Nova Bridge",
 version="1.0.0",
 lifespan=lifespan
)








@app.get("/")
async def root():
 """Health check endpoint"""
 return {
     "service": "Nova Windows Executor",
     "executor_id": EXECUTOR_ID,
     "status": "online",
     "connected_to_bridge": executor_state["connected"],
     "uptime": (datetime.now(timezone.utc) - executor_state["stats"]["start_time"]).total_seconds()
 }








@app.get("/info")
async def get_executor_info():
 """Get detailed executor information"""
 import platform




 return ExecutorInfo(
     executor_id=EXECUTOR_ID,
     status="online",
     connected=executor_state["connected"],
     stats=executor_state["stats"],
     current_jobs=len(executor_state["current_jobs"]),
     system_info={
         "platform": platform.platform(),
         "python_version": sys.version,
         "processor": platform.processor(),
         "machine": platform.machine()
     }
 )








@app.post("/execute")
async def execute_local(request: ExecuteRequest, background_tasks: BackgroundTasks):
 """Execute code locally (for testing without bridge)"""
 job_id = str(uuid.uuid4())




 # Execute in background
 background_tasks.add_task(
     execute_local_job,
     job_id,
     request.code,
     request.files,
     request.timeout
 )




 return {
     "job_id": job_id,
     "status": "submitted",
     "message": "Job submitted for local execution"
 }








async def execute_local_job(job_id: str, code: str, files: List[Dict], timeout: int):
 """Execute job locally without bridge connection"""




 # Create mock websocket that logs results
 class MockWebSocket:
     async def send(self, data):
         result_data = json.loads(data)
         logger.info(f"Local execution result - Status: {result_data.get('status')}, Job: {result_data.get('job_id')}")




 mock_ws = MockWebSocket()




 await execute_job(mock_ws, {
     "job_id": job_id,
     "code": code,
     "files": files,
     "timeout": timeout
 })








@app.get("/job/{job_id}")
async def get_job_status(job_id: str):
 """Get status of a specific job"""
 if job_id in executor_state["current_jobs"]:
     job = executor_state["current_jobs"][job_id]
     return JobStatus(
         job_id=job_id,
         status=job["status"],
         started_at=job["started_at"]
     )
 elif job_id in executor_state["completed_jobs"]:
     job = executor_state["completed_jobs"][job_id]
     return JobStatus(
         job_id=job_id,
         status=job["status"],
         result=job.get("result"),
         started_at=job["started_at"],
         completed_at=job["completed_at"]
     )
 else:
     raise HTTPException(status_code=404, detail="Job not found")








@app.get("/jobs")
async def list_jobs(status: Optional[str] = None):
 """List all jobs or filter by status"""
 jobs = []




 # Add current jobs
 for job_id, job in executor_state["current_jobs"].items():
     if status is None or job["status"] == status:
         jobs.append({
             "job_id": job_id,
             "status": job["status"],
             "started_at": job["started_at"]
         })




 # Add completed jobs
 for job_id, job in executor_state["completed_jobs"].items():
     if status is None or job["status"] == status:
         jobs.append({
             "job_id": job_id,
             "status": job["status"],
             "started_at": job["started_at"],
             "completed_at": job["completed_at"]
         })




 return {
     "jobs": jobs,
     "count": len(jobs)
 }








@app.get("/stats")
async def get_stats():
 """Get executor statistics"""
 stats = executor_state["stats"].copy()
 stats["current_jobs"] = len(executor_state["current_jobs"])
 stats["uptime_seconds"] = (datetime.now(timezone.utc) - stats["start_time"]).total_seconds()
 return stats




@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    target_path: Optional[str] = None
):
    """Direct file upload endpoint - handles large files efficiently"""
    if not target_path:
        # Default to mathcad working directory with original filename
        target_path = os.path.join(MATHCAD_WORKING_DIR, file.filename)
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        
        # Stream file to disk in 1MB chunks
        bytes_written = 0
        async with aiofiles.open(target_path, 'wb') as out_file:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                await out_file.write(chunk)
                bytes_written += len(chunk)
        
        logger.info(f"File uploaded successfully: {target_path} ({bytes_written:,} bytes)")
        
        return {
            "status": "success",
            "path": target_path,
            "size": bytes_written,
            "filename": file.filename
        }
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(500, f"Upload failed: {str(e)}")








@app.post("/shutdown")
async def shutdown_executor():
 """Shutdown the Nova executor (can be called via REST API)"""
 logger.warning("Shutdown requested via REST API")




 # Send shutdown notification if connected to bridge
 if executor_state["websocket"]:
     try:
         await executor_state["websocket"].send(json.dumps({
             "type": "executor_shutting_down",
             "executor_id": EXECUTOR_ID,
             "reason": "REST API shutdown request"
         }))
     except:
         pass




 # Schedule shutdown after response is sent
 asyncio.create_task(shutdown_after_delay())




 return {
     "status": "shutdown_initiated",
     "executor_id": EXECUTOR_ID,
     "message": "Nova executor will shutdown in 2 seconds"
 }








async def shutdown_after_delay():
 """Shutdown after a short delay to allow response to be sent"""
 await asyncio.sleep(2)
 logger.info("Executing shutdown...")
 # Use sys.exit for clean shutdown on Windows
 sys.exit(0)








@app.websocket("/ws/monitor")
async def websocket_monitor(websocket: WebSocket):
 """WebSocket endpoint for real-time monitoring"""
 await websocket.accept()




 try:
     while True:
         # Send status updates every 5 seconds
         await websocket.send_json({
             "type": "status_update",
             "executor_id": EXECUTOR_ID,
             "connected": executor_state["connected"],
             "current_jobs": len(executor_state["current_jobs"]),
             "stats": executor_state["stats"]
         })




         await asyncio.sleep(5)




 except WebSocketDisconnect:
     logger.info("Monitoring client disconnected")








def _collect_mathcad_processes():
    """Fast part: enumerate Mathcad processes without open_files()"""
    mathcad_processes = []
    try:
        process_count = 0
        for proc in psutil.process_iter(['pid', 'name', 'create_time', 'memory_info']):
            process_count += 1
            try:
                proc_name = proc.info.get('name', '')
                if proc_name and 'MathcadPrime' in proc_name:
                    # Get additional process info
                    create_time = datetime.fromtimestamp(proc.info['create_time']).strftime('%Y-%m-%d %H:%M:%S')
                    memory_mb = proc.info['memory_info'].rss / 1024 / 1024  # Convert to MB




                    process_info = {
                        'pid': proc.info['pid'],
                        'name': proc.info['name'],
                        'create_time': create_time,
                        'memory_mb': round(memory_mb, 2),
                        'cpu_percent': 0,  # Default to 0
                        'open_files': []  # Will be filled later
                    }




                    # Try to get CPU percent (quick check)
                    try:
                        cpu_percent = proc.cpu_percent(interval=0.1)
                        process_info['cpu_percent'] = cpu_percent
                    except:
                        pass




                    mathcad_processes.append(process_info)




            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass




        logger.info(f"Checked {process_count} processes, found {len(mathcad_processes)} Mathcad instances")
        return mathcad_processes


    except Exception as e:
        logger.error(f"Error collecting processes: {e}")
        return []








def _get_mcdx_for_pid(pid):
    """Heavy part: get open files for a single process (run in worker thread)"""
    try:
        proc = psutil.Process(pid)
        # Use generator to avoid loading all files if we hit an error
        open_files = []
        for f in proc.open_files():
            if f.path.endswith('.mcdx'):
                open_files.append(f.path)
        return pid, open_files
    except psutil.AccessDenied:
        logger.warning(f"Access denied getting open files for PID {pid} - skipping")
        return pid, []
    except psutil.NoSuchProcess:
        logger.debug(f"Process {pid} no longer exists")
        return pid, []
    except Exception as e:
        logger.error(f"Error getting open files for PID {pid}: {e}", exc_info=True)
        return pid, []








# Note: check_mathcad_status_sync removed - we now use proper async implementation








async def check_mathcad_status():
    """Check if Mathcad is running and get process details (proper async implementation)"""
    try:
        loop = asyncio.get_running_loop()
        
        # 1. Fast snapshot of processes (without open_files)
        mathcad_processes = await loop.run_in_executor(_io_executor, _collect_mathcad_processes)
   
        # 2. Concurrent open_files() probes, bounded by semaphore
        async def probe_open_files(pid):
            async with _io_semaphore:
                return await loop.run_in_executor(_io_executor, _get_mcdx_for_pid, pid)
        
        # Get all open files concurrently
        if mathcad_processes:
            open_files_results = await asyncio.gather(
                *(probe_open_files(p['pid']) for p in mathcad_processes),
                return_exceptions=True
            )
            
            # Merge results back into process info
            open_files_map = {}
            for result in open_files_results:
                if isinstance(result, tuple) and len(result) == 2:
                    pid, files = result
                    open_files_map[pid] = files
            
            # Update process info with open files
            for proc_info in mathcad_processes:
                proc_info['open_files'] = open_files_map.get(proc_info['pid'], [])
        
        # Collect all open files
        all_open_files = []
        for proc in mathcad_processes:
            all_open_files.extend(proc.get('open_files', []))




        # Check working directory
        working_dir_files = []
        if os.path.exists(MATHCAD_WORKING_DIR):
            try:
                all_files = await loop.run_in_executor(
                    _io_executor,
                    lambda: os.listdir(MATHCAD_WORKING_DIR)
                )
                working_dir_files = [f for f in all_files if f.endswith('.mcdx')]
            except Exception as e:
                logger.debug(f"Error listing working directory: {e}")




        # Create result dictionary
        result = {
            'is_running': len(mathcad_processes) > 0,
            'processes': mathcad_processes,
            'open_files': all_open_files,
            'working_dir_files': working_dir_files
        }




        return result




    except Exception as e:
        logger.error(f"Fatal error in status check: {e}")
        # Return default on error
        return {
            'is_running': False,
            'processes': [],
            'open_files': [],
            'working_dir_files': [],
            'error': str(e)
        }








async def update_mathcad_status_cache():
 """Update the Mathcad status cache (non-blocking)"""
 global mathcad_status_cache




 # Use proper lock instead of boolean flag
 async with mathcad_status_lock:
     logger.info("Updating Mathcad status cache...")




     try:
         # Just call check_mathcad_status directly - it already uses thread pool
         status = await check_mathcad_status()




         # Update cache atomically
         mathcad_status_cache["status"] = status
         mathcad_status_cache["timestamp"] = datetime.now(timezone.utc)




         logger.info(f"Mathcad status cache updated: is_running={status['is_running']}, "
                     f"processes={len(status['processes'])}")
     except Exception as e:
         logger.error(f"Failed to update Mathcad status cache: {e}", exc_info=True)








async def handle_mathcad_status_request(websocket, force_refresh=False, ws_lock=None):
 """Handle Mathcad status request from Mac"""
 global mathcad_status_cache




 # Check if cache is valid
 cache_valid = False
 if mathcad_status_cache["status"] and mathcad_status_cache["timestamp"]:
     age = (datetime.now(timezone.utc) - mathcad_status_cache["timestamp"]).total_seconds()
     cache_valid = age < mathcad_status_cache["cache_duration"]




 # Use cache or refresh
 if cache_valid and not force_refresh:
     logger.info("Returning cached Mathcad status")
     status = mathcad_status_cache["status"]
     cache_age = (datetime.now(timezone.utc) - mathcad_status_cache["timestamp"]).total_seconds()
 else:
     logger.info(f"Refreshing Mathcad status (force_refresh={force_refresh})")
     await update_mathcad_status_cache()
     status = mathcad_status_cache["status"]
     cache_age = 0




 # Send response
 response = {
     "type": "mathcad_status_result",
     "status": status,
     "cache_age": round(cache_age, 1),
     "from_cache": cache_valid and not force_refresh,
     "timestamp": datetime.now(timezone.utc).isoformat()
 }




 try:
     if ws_lock:
         async with ws_lock:
             await websocket.send(json.dumps(response))
     else:
         await websocket.send(json.dumps(response))
     logger.info(f"Sent Mathcad status: is_running={status['is_running']}, "
                 f"from_cache={response['from_cache']}, cache_age={cache_age}s")
 except Exception as e:
     logger.error(f"Failed to send Mathcad status: {e}")








async def background_mathcad_monitor():
 """Background task to monitor Mathcad status every 5 minutes"""
 logger.info("Starting background Mathcad status monitor")




 try:
     while True:
         try:
             # Wait 5 minutes
             await asyncio.sleep(300)




             # Update cache if connected
             if executor_state["connected"]:
                 logger.debug("Background Mathcad status check")  # Changed from info to debug
                 await update_mathcad_status_cache()
         except asyncio.CancelledError:
             raise  # Re-raise to be caught by outer handler
         except Exception as e:
             logger.error(f"Error in background Mathcad monitor: {e}")
             await asyncio.sleep(10)  # Short delay on error
 except asyncio.CancelledError:
     logger.info("Background Mathcad monitor cancelled - shutting down gracefully")
     return








# SAP2000 REST endpoints
@app.post("/sap/connect")
async def sap_connect():
    """Connect to SAP2000 and create COM object"""
    global sap2000_state
    if sap2000_state["is_connected"] and sap2000_state["instance"]:
        return {
            "status": "already_connected",
            "message": "SAP2000 is already connected"
        }
    try:
        logger.info("Connecting to SAP2000...")
        start_time = datetime.now(timezone.utc)
        
        # Run COM operations in thread
        def _connect_sap():
            # Import comtypes
            import comtypes.client
            
            # Create API helper object
            helper = comtypes.client.CreateObject('SAP2000v1.Helper')
            helper = helper.QueryInterface(comtypes.gen.SAP2000v1.cHelper)
            
            # Create SAP2000 instance
            sap_object = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
            
            # Start SAP2000 application
            ret = sap_object.ApplicationStart()
            
            if ret == 0:
                # Make visible
                sap_object.Visible = True
                return sap_object, sap_object.SapModel, ret
            else:
                return None, None, ret
        
        # Execute in thread
        sap_object, sap_model, ret = await to_thread(_connect_sap)
        
        if ret == 0 and sap_object:
            # Store references
            sap2000_state["instance"] = sap_object
            sap2000_state["model"] = sap_model
            sap2000_state["is_connected"] = True
            sap2000_state["last_activity"] = datetime.now(timezone.utc)
            
            # Calculate launch time
            launch_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.info(f"SAP2000 connected successfully in {launch_time:.2f} seconds")
            
            return {
                "status": "connected",
                "message": "SAP2000 started successfully",
                "launch_time_seconds": round(launch_time, 2)
            }
        else:
            return {
                "status": "error",
                "message": f"Failed to start SAP2000, return code: {ret}"
            }
            
    except Exception as e:
        logger.error(f"Error connecting to SAP2000: {e}")
        return {
            "status": "error",
            "message": str(e)
        }








@app.post("/sap/api")
async def sap_api(request: ExecuteRequest):
    """Execute SAP2000 API code on persistent instance"""
    global sap2000_state
    if not sap2000_state["is_connected"] or not sap2000_state["instance"]:
        return {
            "status": "not_connected",
            "message": "SAP2000 is not connected. Call /sap/connect first"
        }
    try:
        # Update activity timestamp
        sap2000_state["last_activity"] = datetime.now(timezone.utc)
        
        # Create execution context with SAP objects
        exec_globals = {
            'mySapObject': sap2000_state["instance"],
            'SapModel': sap2000_state["model"],
            'ret': None  # Common return variable
        }
        
        # Execute the code in thread to avoid blocking
        await to_thread(exec, request.code, exec_globals)
        
        # Extract any variables that were set
        results = {}
        for key, value in exec_globals.items():
            if key not in ['mySapObject', 'SapModel', '__builtins__']:
                # Try to make value JSON serializable
                try:
                    if hasattr(value, '__iter__') and not isinstance(value, str):
                        results[key] = list(value)
                    else:
                        results[key] = value
                except:
                    results[key] = str(value)
        
        return {
            "status": "success",
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Error executing SAP API: {e}")
        return {
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc()
        }








@app.get("/sap/status")
async def sap_status():
    """Get SAP2000 connection status"""
    global sap2000_state
    status = {
        "is_connected": sap2000_state["is_connected"],
        "has_instance": sap2000_state["instance"] is not None,
        "model_path": sap2000_state["model_path"],
        "last_activity": sap2000_state["last_activity"].isoformat() if sap2000_state["last_activity"] else None
    }
    if sap2000_state["is_connected"] and sap2000_state["instance"]:
        try:
            # Get additional info in thread to avoid blocking
            def _get_sap_info():
                version = sap2000_state["instance"].SapModel.GetVersion()
                version_str = version[0] if version else "Unknown"
                
                # Get current filename
                filename = sap2000_state["model"].GetModelFilename()
                return version_str, filename
            
            version_str, filename = await to_thread(_get_sap_info)
            status["version"] = version_str
            status["current_file"] = filename if filename else None
            
        except Exception as e:
            logger.warning(f"Error getting SAP status details: {e}")
    return status








@app.post("/sap/disconnect")
async def sap_disconnect():
    """Disconnect from SAP2000 and clean up"""
    global sap2000_state
    if not sap2000_state["is_connected"]:
        return {
            "status": "not_connected",
            "message": "SAP2000 is not connected"
        }
    try:
        # Close SAP2000
        if sap2000_state["instance"]:
            sap2000_state["instance"].ApplicationExit(False)  # False = don't save
        
        # Clear state
        sap2000_state["instance"] = None
        sap2000_state["model"] = None
        sap2000_state["is_connected"] = False
        sap2000_state["model_path"] = None
        sap2000_state["last_activity"] = None
        
        logger.info("SAP2000 disconnected successfully")
        
        return {
            "status": "disconnected",
            "message": "SAP2000 closed successfully"
        }
        
    except Exception as e:
        logger.error(f"Error disconnecting SAP2000: {e}")
        # Force clear state even on error
        sap2000_state["instance"] = None
        sap2000_state["model"] = None
        sap2000_state["is_connected"] = False
        
        return {
            "status": "error",
            "message": str(e)
        }








if __name__ == "__main__":
    import uvicorn




    # Log startup configuration
    logger.info(f"Starting with configuration:")
    logger.info(f"  EXECUTOR_ID: {EXECUTOR_ID}")
    logger.info(f"  NOVA_API_URL: {NOVA_API_URL}")
    logger.info(f"  MATHCAD_WORKING_DIR: {MATHCAD_WORKING_DIR}")
    logger.info(f"  MAX_EXECUTION_TIME: {MAX_EXECUTION_TIME}s")




    uvicorn.run(app, host="0.0.0.0", port=8000)
    # uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

