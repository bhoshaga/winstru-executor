"""
SAP2000 Manager Class
Handles all SAP2000 COM operations for the Nova Bridge executor
"""

import asyncio
import sys
import traceback
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import comtypes.client


class SAP2000Manager:
    """Manages SAP2000 connection and API operations"""

    def __init__(self):
        """Initialize SAP2000 manager with state tracking"""
        self.instance = None          # SAP2000 COM object (SapObject)
        self.model = None              # SapModel object
        self.is_connected = False
        self.model_path = None
        self.last_activity = None
        self.connection_time = None

    async def connect(self, timeout: int = 30) -> Dict[str, Any]:
        """
        Connect to SAP2000 and create COM objects
        Returns: {"status": "connected/error/already_connected", "message": str, ...}
        """
        if self.is_connected and self.instance:
            return {
                "status": "already_connected",
                "message": "SAP2000 is already connected"
            }

        try:
            start_time = datetime.now(timezone.utc)

            # Run COM operations in thread to avoid blocking
            loop = asyncio.get_running_loop()
            sap_object, sap_model, ret = await asyncio.wait_for(
                loop.run_in_executor(None, self._connect_sap),
                timeout=timeout
            )

            if ret == 0 and sap_object:
                # Store references
                self.instance = sap_object
                self.model = sap_model
                self.is_connected = True
                self.connection_time = start_time
                self.last_activity = datetime.now(timezone.utc)

                # Calculate launch time
                launch_time = (datetime.now(timezone.utc) - start_time).total_seconds()

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

        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": f"Connection timeout after {timeout} seconds"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }

    async def execute(self, code: str, timeout: int = 60) -> Dict[str, Any]:
        """
        Execute SAP2000 API code with persistent objects
        Returns: {"status": "success/error/not_connected", "results": {...}, ...}
        """
        if not self.is_connected or not self.instance:
            return {
                "status": "not_connected",
                "message": "SAP2000 is not connected. Call connect first"
            }

        try:
            start_time = datetime.now(timezone.utc)
            self.last_activity = start_time

            # Run code execution in thread
            loop = asyncio.get_running_loop()
            results = await asyncio.wait_for(
                loop.run_in_executor(None, self._execute_code, code),
                timeout=timeout
            )

            execution_time = (datetime.now(timezone.utc) - start_time).total_seconds()

            return {
                "status": "success",
                "results": results,
                "execution_time": round(execution_time, 3)
            }

        except asyncio.TimeoutError:
            return {
                "status": "error",
                "message": f"Execution timeout after {timeout} seconds"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current connection status and model info
        Returns: {"is_connected": bool, "has_instance": bool, "version": str, ...}
        """
        status = {
            "is_connected": self.is_connected,
            "has_instance": self.instance is not None,
            "model_path": self.model_path,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None
        }

        if self.connection_time:
            duration = (datetime.now(timezone.utc) - self.connection_time).total_seconds()
            status["connection_duration_minutes"] = round(duration / 60, 2)

        if self.is_connected and self.instance:
            try:
                # Get version and filename in thread
                loop = asyncio.get_running_loop()
                version_str, filename = await loop.run_in_executor(
                    None, self._get_model_info
                )
                status["version"] = version_str
                status["current_file"] = filename if filename else None

            except Exception as e:
                status["error"] = f"Error getting model info: {str(e)}"

        return status

    async def disconnect(self, save: bool = False) -> Dict[str, Any]:
        """
        Disconnect SAP2000 and clean up
        Returns: {"status": "disconnected/error/not_connected", "message": str}
        """
        if not self.is_connected:
            return {
                "status": "not_connected",
                "message": "SAP2000 is not connected"
            }

        try:
            # Close SAP2000 in thread
            if self.instance:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    self.instance.ApplicationExit,
                    save
                )

            # Clear state
            self._cleanup_state()

            return {
                "status": "disconnected",
                "message": f"SAP2000 closed successfully (saved={save})"
            }

        except Exception as e:
            # Force clear state even on error
            self._cleanup_state()

            return {
                "status": "error",
                "message": str(e)
            }

    async def open_file(self, file_path: str) -> Dict[str, Any]:
        """
        Open a SAP2000 model file
        Returns: {"status": "success/error", "message": str, "file_path": str}
        """
        if not self.is_connected or not self.model:
            return {
                "status": "not_connected",
                "message": "SAP2000 is not connected"
            }

        try:
            self.last_activity = datetime.now(timezone.utc)

            # Open file in thread
            loop = asyncio.get_running_loop()
            ret = await loop.run_in_executor(
                None,
                self.model.File.OpenFile,
                file_path
            )

            if ret == 0:
                self.model_path = file_path
                return {
                    "status": "success",
                    "message": f"Opened file: {file_path}",
                    "file_path": file_path
                }
            else:
                return {
                    "status": "error",
                    "message": f"Failed to open file, return code: {ret}",
                    "file_path": file_path
                }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "file_path": file_path
            }

    async def save_file(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Save current model (to new path or existing)
        Returns: {"status": "success/error", "message": str, "file_path": str}
        """
        if not self.is_connected or not self.model:
            return {
                "status": "not_connected",
                "message": "SAP2000 is not connected"
            }

        try:
            self.last_activity = datetime.now(timezone.utc)

            # Save file in thread
            loop = asyncio.get_running_loop()
            if file_path:
                # Save to new location
                ret = await loop.run_in_executor(
                    None,
                    self.model.File.Save,
                    file_path
                )
                if ret == 0:
                    self.model_path = file_path
            else:
                # Save to current location
                ret = await loop.run_in_executor(
                    None,
                    self.model.File.Save
                )
                file_path = self.model_path

            if ret == 0:
                return {
                    "status": "success",
                    "message": f"Saved file: {file_path}",
                    "file_path": file_path
                }
            else:
                return {
                    "status": "error",
                    "message": f"Failed to save file, return code: {ret}",
                    "file_path": file_path
                }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "file_path": file_path
            }

    # Private helper methods
    def _connect_sap(self) -> tuple:
        """Internal: Perform COM connection in thread"""
        # Import comtypes and initialize COM for this thread
        import comtypes
        import comtypes.client

        # Initialize COM for this thread (required when using thread pool)
        comtypes.CoInitialize()

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

    def _execute_code(self, code: str) -> Dict[str, Any]:
        """Internal: Execute code with SAP objects in context"""
        # Create execution context with SAP objects
        exec_globals = {
            'mySapObject': self.instance,
            'SapModel': self.model,
            'ret': None  # Common return variable
        }

        # Execute the code
        exec(code, exec_globals)

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

        return results

    def _get_model_info(self) -> tuple:
        """Internal: Get version and filename from model"""
        version = self.instance.SapModel.GetVersion()
        version_str = version[0] if version else "Unknown"

        # Get current filename
        filename = self.model.GetModelFilename()
        return version_str, filename

    def _cleanup_state(self):
        """Internal: Clear all state variables"""
        self.instance = None
        self.model = None
        self.is_connected = False
        self.model_path = None
        self.last_activity = None
        self.connection_time = None