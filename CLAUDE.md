# CLAUDE.md - Important Implementation Notes

## Critical WebSocket Response Format Requirement

### ⚠️ ALL WebSocket responses MUST use `type: "result"`

The Nova Bridge **requires** all response messages to have `"type": "result"` for proper routing. Any other type will cause the bridge to fail to route responses back to the client.

#### ✅ CORRECT Format:
```json
{
    "type": "result",           // MUST be "result" - no exceptions!
    "job_id": "unique-uuid",
    "status": "completed",      // or "failed", "timeout", etc.
    "executor_id": "windows_lite_...",
    "result": {                 // All custom data goes here
        // Your operation-specific data
    }
}
```

#### ❌ INCORRECT Format (will break routing):
```json
{
    "type": "sap_result",       // WRONG - bridge won't route this
    "job_id": "unique-uuid",
    // ...
}
```

```json
{
    "type": "custom_response",  // WRONG - bridge won't route this
    "job_id": "unique-uuid",
    // ...
}
```

### Examples of Correct Responses:

#### SAP Connect Response:
```json
{
    "type": "result",
    "job_id": "uuid-123",
    "status": "completed",
    "executor_id": "windows_lite_abc",
    "result": {
        "operation": "connect",
        "status": "connected",
        "message": "SAP2000 started successfully"
    }
}
```

#### Python Execution Response:
```json
{
    "type": "result",
    "job_id": "uuid-456",
    "status": "completed",
    "executor_id": "windows_lite_abc",
    "result": {
        "stdout": "Output here",
        "stderr": "",
        "return_code": 0
    }
}
```

### Rule Summary:
- **ALWAYS** use `"type": "result"` for ALL responses
- Put operation-specific data inside the `"result"` field
- This applies to ALL operations: execute, SAP operations, status checks, etc.
- Without this, the bridge cannot route messages back to the Mac client

---

## Other Important Notes

### Running Tests and Linting
When implementing new features, always run:
- Lint command: `npm run lint` (if available)
- Type check: `npm run typecheck` (if available)
- Tests: Check README for test commands

### SAP2000 Integration
- All SAP operations use WebSocket messages (not HTTP)
- SAP state is maintained in the Windows executor process
- COM objects persist between operations until disconnect