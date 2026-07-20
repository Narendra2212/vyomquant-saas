import httpx
import logging
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_backend_create_strategy():
    email = "your-test-email@example.com"
    password = os.environ.get("TEST_USER_PASSWORD", "YOUR_TEST_PASSWORD")
    
    async with httpx.AsyncClient(verify=False) as client:
        # Login via backend
        login_resp = await client.post("http://127.0.0.1:8000/api/auth/login", json={
            "email": email,
            "password": password
        })
        
        if login_resp.status_code != 200:
            logger.error(f"Login failed: {login_resp.text}")
            return
            
        token = login_resp.json().get("access_token")
        
        # Create strategy
        headers = {"Authorization": f"Bearer {token}"}
        strategy_payload = {
            "name": "Backend Test Strategy",
            "symbol": "BTC/USDT",
            "timeframe": "1h"
        }
        
        create_resp = await client.post(
            "http://127.0.0.1:8000/api/strategies/",
            json=strategy_payload,
            headers=headers
        )
        
        logger.info(f"Create strategy status: {create_resp.status_code}")
        logger.info(f"Create strategy response: {create_resp.text}")

if __name__ == "__main__":
    asyncio.run(test_backend_create_strategy())
