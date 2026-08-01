---
name: sync-api-layer
description: Sync API Layer - Safely updates frontend connection layer with strict GUI preservation via Adapter Pattern
---

# Sync API Layer Workflow

Safely updates the frontend connection layer based on a selected backend controller, ensuring strict GUI preservation via the Adapter Pattern.

## Step 1: Context Gathering

Analyze the currently open backend file. Extract all endpoint definitions, expected request bodies, and response schemas. Output this as a structured JSON representation in the chat.

**Required Output:**
- Endpoint URLs and HTTP methods
- Request/response DTOs (Pydantic models)
- Query parameters and validation rules

## Step 2: Connection Layer Identification

Search the workspace for the corresponding frontend service layer file that interacts with these endpoints. Do not modify anything yet. Present the file path for confirmation.

**Search Patterns:**
- `api.js`, `service.ts`, `client.ts` files
- Look for matching endpoint URL patterns
- Identify existing type definitions

## Step 3: Adapter Implementation

Update the identified frontend service layer. You must use an Adapter pattern.

**Strict Constraints:**
1. Create `Backend*Response` type matching FastAPI schema exactly
2. Retain existing UI type (e.g., `UIPortfolioState`) — DO NOT MODIFY
3. Implement `mapBackendToUI*()` adapter function with:
   - Field mapping (handle snake_case → camelCase if needed)
   - Null/undefined fallbacks
   - Type validation
4. Transform the new backend response into the exact data structure the current UI expects
5. **DO NOT touch any .tsx, .jsx, .vue, or .html files representing the UI**
6. Ensure strict TypeScript/JSDoc types are maintained

## Step 4: Test Generation

Generate a unit test for the newly created Adapter function to ensure data integrity during transformation.

**Test Requirements:**
- Happy path (valid backend response)
- Null/undefined handling
- Missing fields (partial response)
- Type coercion edge cases
- Field name variations (snake_case vs camelCase)

## Step 5: Validation

Run build verification and confirm:
- Zero GUI changes
- No TypeScript/compilation errors
- Adapter function exported correctly

**Command:**
```bash
npm run build
```

## Exclusion List (Never Implement)

The following endpoints are quarantined for Admin/God mode only:

- GET `/api/security/logs`
- GET `/api/admin/health`
- GET `/api/admin/metrics`
- GET `/api/admin/users`
- POST `/api/admin/users/{user_id}/status`
- POST `/api/admin/kill-all`
- POST `/api/admin/reinitialize`
- GET `/api/admin/fleet-status`
- GET `/health`
