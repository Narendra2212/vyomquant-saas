# DEPLOY REQUEST SCHEMA

## Route Information
* **Endpoint:** `POST /api/strategies/{id}/deploy`
* **File:** `routers/strategies.py` (Line ~800)
* **Controller:** `deploy_bot`

## Request Model
* **Model Name:** `DeployRequest`
* **File:** `core/models/pydantic_models.py`

## Schema Definition
### Required Fields
1. `exchange_id` (str)

### Optional Fields
None natively mapped.

## Deployment Lifecycle
1. The route receives `strategy_id` from the path parameter and `exchange_id` from the body.
2. It fetches the strategy blueprint from Supabase using `strategy_id`.
3. It passes `user["id"]`, `symbol`, and `blueprint` into `fleet.start_bot(user["id"], symbol, blueprint)`.
4. It updates the database setting `status = "running"`.
5. It broadcasts a WebSocket message to the client.

## Example Request
**URL:** `POST /api/strategies/b42cf9a-1122/deploy`
**Body:**
```json
{
  "exchange_id": "binance"
}
```

## Example Response
```json
{
  "status": "running"
}
```
