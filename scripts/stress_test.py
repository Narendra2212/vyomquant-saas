from locust import HttpUser, task, between


class Algo22StressTest(HttpUser):
    # Simulates hyper-fast algorithmic pinging (100ms to 500ms between requests)
    wait_time = between(0.1, 0.5)

    @task
    def ping_engine(self):
        # Attacking the public health route to test raw server speed
        self.client.get("/health")
