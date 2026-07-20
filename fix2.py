with open('test_live_telemetry_pipeline.py', 'r') as f:
    c = f.read()

c = c.replace('async def ws_listener(captured_events):', 'async def ws_listener(captured_events, ready_event):')
c = c.replace('print("WS Client Subscribed. Listening for live telemetry...")', 'print("WS Client Subscribed. Listening for live telemetry...")\n            ready_event.set()')
c = c.replace('ws_task = asyncio.create_task(ws_listener(captured_events))', 'ready_event = asyncio.Event()\n    ws_task = asyncio.create_task(ws_listener(captured_events, ready_event))')
c = c.replace('await asyncio.sleep(2)', 'await ready_event.wait()', 1)

with open('test_live_telemetry_pipeline.py', 'w') as f:
    f.write(c)
print('Fixed ws_listener sync')
