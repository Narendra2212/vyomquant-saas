import os

import redis
from dotenv import load_dotenv
from rq import Connection, Queue, Worker

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Create Redis connection
redis_conn = redis.from_url(REDIS_URL)

# Create Queue
task_queue = Queue("backtest", connection=redis_conn)

if __name__ == '__main__':
    print("Starting background worker for backtest queue...")
    with Connection(redis_conn):
        worker = Worker([task_queue])
        worker.work()
