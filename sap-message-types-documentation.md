# SAP2000 WebSocket Message Types Documentation

## Overview
The Windows executor (`client.py`) supports 6 SAP2000 message types that allow full control over SAP2000 via WebSocket. The executor maintains a persistent SAP2000 connection and state between messages.

---

## 1. `sap_connect`
**Purpose:** Launch SAP2000 and establish COM connection

### What it does:
- Creates a new SAP2000 instance (always creates new, never connects to existing)
- Starts the SAP2000 application via COM automation
- Makes SAP2000 window visible
- Stores the COM objects (`SapObject` and `SapModel`) in memory
- Maintains these objects until explicitly disconnected

### Request:
```json
{
    "type": "sap_connect",
    "job_id": "unique-uuid",
    "timeout": 30  // optional, defaults to 30 seconds
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "connected",  // or "already_connected", "error"
        "message": "SAP2000 started successfully",
        "launch_time_seconds": 5.2
    }
}
```

### State Management:
- **Before:** No SAP2000 instance
- **After:** SAP2000 running, COM objects stored in `sap_manager`
- **Persistence:** Connection remains until `sap_disconnect` or executor shutdown

### Edge Cases:
- If already connected: Returns `"status": "already_connected"`
- If SAP2000 not installed: Returns error with details
- If timeout: Returns error after specified seconds

---

## 2. `sap_execute`
**Purpose:** Execute SAP2000 API code on the connected instance

### What it does:
- Takes Python code as string
- Executes it with pre-defined SAP objects in context
- Returns any variables that were created/modified
- Maintains connection and state after execution

### Available Variables in Code:
- `SapModel` - The SAP2000 model object (for all API calls)
- `mySapObject` - The SAP2000 application object
- `ret` - Common return variable (convention)

### Request:
```json
{
    "type": "sap_execute",
    "job_id": "unique-uuid",
    "code": "ret = SapModel.File.NewBlank()\nSapModel.SetPresentUnits(4)",
    "timeout": 60  // optional, defaults to 60 seconds
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "success",  // or "error", "not_connected"
        "results": {
            "ret": 0,  // Any variables set in the code
            "NumberResults": 10,
            "LoadCase": ["DEAD", "LIVE"]
        },
        "execution_time": 0.234
    }
}
```

### State Management:
- **Requires:** Active SAP2000 connection from `sap_connect`
- **Preserves:** All SAP2000 state (open model, settings, etc.)
- **Returns:** All non-system variables created during execution

### Example Code Snippets:
```python
# Create new blank model
"ret = SapModel.File.NewBlank()"

# Set units to kip-ft
"ret = SapModel.SetPresentUnits(4)"

# Define material property
"ret = SapModel.PropMaterial.SetMaterial('STEEL', 1)"

# Add frame section
"ret = SapModel.PropFrame.SetRectangle('R1', 'STEEL', 12, 24)"

# Get analysis results
"ret, NumberResults, Obj, Elm, LoadCase, StepType, StepNum, U1, U2, U3, R1, R2, R3 = SapModel.Results.Setup.DeselectAllCasesAndCombosForOutput()"
```

---

## 3. `sap_status`
**Purpose:** Check connection status and get SAP2000 information

### What it does:
- Checks if SAP2000 is connected
- Retrieves version information
- Gets current open file (if any)
- Returns connection duration
- Non-destructive (read-only operation)

### Request:
```json
{
    "type": "sap_status",
    "job_id": "unique-uuid"
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "is_connected": true,
        "has_instance": true,
        "version": "24.0.0",
        "current_file": "C:\\Models\\Bridge.sdb",  // null if no file open
        "model_path": "C:\\Models\\Bridge.sdb",
        "last_activity": "2024-01-15T10:30:45.123Z",
        "connection_duration_minutes": 15.5
    }
}
```

### State Management:
- **Read-only:** Does not modify any state
- **Safe to call:** Can be called anytime, even if not connected

---

## 4. `sap_disconnect`
**Purpose:** Close SAP2000 and clean up resources

### What it does:
- Closes the SAP2000 application
- Optionally saves before closing
- Releases COM objects
- Clears all stored state
- Frees memory

### Request:
```json
{
    "type": "sap_disconnect",
    "job_id": "unique-uuid",
    "save": false  // optional, defaults to false
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "disconnected",  // or "not_connected", "error"
        "message": "SAP2000 closed successfully (saved=false)"
    }
}
```

### State Management:
- **Before:** SAP2000 running with active connection
- **After:** SAP2000 closed, all state cleared
- **Save option:** If `true`, saves current model before closing

---

## 5. `sap_open_file`
**Purpose:** Open a SAP2000 model file (.sdb, .$2k, .s2k, etc.)

### What it does:
- Opens specified model file in current SAP2000 instance
- Replaces any currently open model
- Updates internal model path tracking
- Makes the model active for subsequent operations

### Request:
```json
{
    "type": "sap_open_file",
    "job_id": "unique-uuid",
    "file_path": "C:\\Models\\Bridge.sdb"
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "success",  // or "error", "not_connected"
        "message": "Opened file: C:\\Models\\Bridge.sdb",
        "file_path": "C:\\Models\\Bridge.sdb"
    }
}
```

### State Management:
- **Requires:** Active SAP2000 connection
- **Replaces:** Any currently open model
- **Updates:** Internal tracking of current file path

### Supported File Types:
- `.sdb` - SAP2000 database files
- `.$2k` - SAP2000 text files
- `.s2k` - SAP2000 text files
- `.xls`, `.xlsx` - Excel files (with proper formatting)
- `.mdb` - Access database files

---

## 6. `sap_save_file`
**Purpose:** Save the current SAP2000 model

### What it does:
- Saves current model to specified path (Save As)
- Or saves to current path if no path specified (Save)
- Updates internal path tracking if saving to new location
- Preserves model state for continued work

### Request:
```json
{
    "type": "sap_save_file",
    "job_id": "unique-uuid",
    "file_path": "C:\\Models\\Bridge_v2.sdb"  // optional, saves to current if omitted
}
```

### Response:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "success",  // or "error", "not_connected"
        "message": "Saved file: C:\\Models\\Bridge_v2.sdb",
        "file_path": "C:\\Models\\Bridge_v2.sdb"
    }
}
```

### State Management:
- **Requires:** Active SAP2000 connection
- **Preserves:** Model remains open after save
- **Updates:** File path if saving to new location

---

## Important Notes

### Connection Lifecycle
1. **Start:** Call `sap_connect` once to launch SAP2000
2. **Work:** Use `sap_execute` multiple times for API operations
3. **Files:** Use `sap_open_file` and `sap_save_file` as needed
4. **Monitor:** Call `sap_status` to check state
5. **End:** Call `sap_disconnect` to close SAP2000

### State Persistence
- SAP2000 instance persists between messages
- All model data, settings, and results remain in memory
- Connection survives until explicit disconnect or executor shutdown
- Multiple `sap_execute` calls work on the same model

### Error Handling
All operations return consistent error format:
```json
{
    "type": "result",
    "job_id": "unique-uuid",
    "executor_id": "windows_lite_abc123",
    "result": {
        "status": "error",
        "message": "Detailed error message",
        "traceback": "Full Python traceback..."
    }
}
```

### Performance Considerations
- `sap_connect`: 5-15 seconds (SAP2000 startup time)
- `sap_execute`: <1 second for most operations
- `sap_status`: Instant (just checking state)
- `sap_open_file`: Depends on file size (1-30 seconds)
- `sap_save_file`: Depends on model complexity (1-10 seconds)
- `sap_disconnect`: 1-2 seconds

### Concurrency
- All SAP operations are sequential (SAP2000 COM is not thread-safe)
- Regular Python `execute` messages can run in parallel
- SAP operations queue if multiple arrive simultaneously