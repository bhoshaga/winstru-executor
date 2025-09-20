# SAP-CLI Backend Integration Questions

## Overview
We're creating a new `sap-cli` tool that will use `nova-lite` to control SAP2000. The CLI will send Python code via nova-lite to be executed on Windows.

## Current Proposed Architecture

### How sap-cli will work:
1. User runs: `sap-cli connect`
2. sap-cli executes via nova-lite: Python code containing SAP2000 COM automation
3. The Python code runs on Windows and creates/maintains SAP objects
4. State persists in nova-lite daemon between commands

### Key Design Decisions:

**No HTTP Server Required** - Unlike the current nova approach which uses HTTP calls to localhost:8000, we plan to execute SAP COM code directly via nova-lite.

## Code Examples of What We'll Send

### For `sap-cli connect`:
```python
import comtypes.client

# Global state for persistence
if 'sap2000_state' not in globals():
    sap2000_state = {
        "instance": None,
        "model": None,
        "is_connected": False
    }

# Create API helper object
helper = comtypes.client.CreateObject('SAP2000v1.Helper')
helper = helper.QueryInterface(comtypes.gen.SAP2000v1.cHelper)

# Create and start SAP2000
sap_object = helper.CreateObjectProgID("CSI.SAP2000.API.SapObject")
ret = sap_object.ApplicationStart()
sap_object.Visible = True

# Store in global state
sap2000_state["instance"] = sap_object
sap2000_state["model"] = sap_object.SapModel
sap2000_state["is_connected"] = True
```

### For `sap-cli api "SapModel.File.NewBlank()"`:
```python
# Execute user's code with access to SapModel
exec_globals = {
    'SapModel': sap2000_state["model"],
    'mySapObject': sap2000_state["instance"]
}
exec("ret = SapModel.File.NewBlank()", exec_globals)
```

## Questions for Backend Team

### 1. State Persistence
**Question:** Will nova-lite daemon preserve Python global variables (like `sap2000_state`) between different command executions? Or does each execution start fresh?

**Our Need:** We need to maintain the SAP2000 COM objects between commands (connect once, then run multiple API commands on the same instance).

### 2. COM Object Lifetime
**Question:** Are there any issues with keeping COM objects (like SapObject and SapModel) alive in memory between nova-lite executions? Any cleanup we should handle?

### 3. Current SAP Work
**Question:** You mentioned you're doing something for SAP right now. Are you:
- Setting up a SAP2000 server/handler on Windows?
- Creating helper functions for SAP operations?
- Something else?

We want to make sure our approaches are compatible.

### 4. Import Dependencies
**Question:** Can we rely on `comtypes` being available when nova-lite executes Python code? Any other imports we should know about?

### 5. Error Handling
**Question:** What's the best way to handle and return errors from nova-lite executions? Should we:
- Use sys.exit(1) for errors?
- Print JSON and parse it?
- Use exceptions?

### 6. Alternative Approach?
**Question:** Would you prefer we use a different approach, such as:
- Having nova-lite call the existing localhost:8000 server endpoints?
- Creating a separate persistent SAP handler that nova-lite can communicate with?
- Something else?

## Our Requirements

1. **Persistent Connection**: Need to connect to SAP2000 once and keep it running
2. **Multiple Commands**: Run many API commands on the same SAP instance
3. **State Tracking**: Know if SAP is connected, what file is open, etc.
4. **Fast Execution**: Avoid reconnecting to SAP for each command (it takes 15+ seconds)

## Proposed Timeline

We're ready to implement once we understand:
1. How state persistence works in nova-lite
2. Any Windows-side changes you're making for SAP
3. Your preferred approach

Please let us know if our proposed direct COM approach via nova-lite will work, or if we should adjust our design.

## Example Commands We'll Support

```bash
sap-cli connect                          # Launch SAP2000
sap-cli api "SapModel.File.NewBlank()"  # Create new model
sap-cli api "SapModel.SetPresentUnits(4)" # Set units
sap-cli open "C:\\Models\\Bridge.sdb"   # Open file
sap-cli save                             # Save current
sap-cli disconnect                       # Close SAP2000
```

---

**Please provide feedback on this approach and let us know what you're currently implementing for SAP support.**