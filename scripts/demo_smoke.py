"""Exercise a running local Mock backend without external services."""
import os
import time
import httpx


def main():
    with httpx.Client(base_url=os.getenv('DEMO_BASE_URL', 'http://127.0.0.1:8000'), timeout=10) as client:
        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def completed(task_id):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                task = request('GET', f'/api/tasks/{task_id}')
                if task['status'] == 'succeeded':
                    return task
                assert task['status'] not in ('failed', 'cancelled', 'timed_out'), task
                time.sleep(.05)
            raise AssertionError('Task completion timeout')

        assert request('GET', '/api/health')['backend'] == 'mock'
        request('POST', '/api/simulation/recover', json={'scene': 'living_room'})
        task = request('POST', '/api/commands', json={'text': '帮我联系家人'})['task']
        assert task['status'] == 'awaiting_confirmation'
        request('POST', f'/api/tasks/{task["id"]}/confirm')
        assert completed(task['id'])['result']['delivery_status'] == 'simulated'
        task = request('POST', '/api/commands', json={'text': '去客厅'})['task']
        completed(task['id'])
        task = request('POST', '/api/commands', json={'text': '去卧室'})['task']
        request('POST', '/api/emergency-stop')
        assert request('GET', f'/api/tasks/{task["id"]}')['status'] == 'cancelled'
        request('POST', '/api/simulation/recover', json={'scene': 'living_room'})
        print('Smoke passed: confirmed simulated help, motion, emergency stop, recovery')


if __name__ == '__main__':
    main()
