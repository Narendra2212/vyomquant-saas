with open('d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/routers/strategies.py', 'a', encoding='utf-8') as f:
    f.write('\n')
    f.write('@router.post("/{strategy_id}/pause")\n')
    f.write('async def pause_strategy_stub(strategy_id: str):\n')
    f.write('    return {"status": "paused", "message": "Strategy paused"}\n\n')
    f.write('@router.post("/{strategy_id}/resume")\n')
    f.write('async def resume_strategy_stub(strategy_id: str):\n')
    f.write('    return {"status": "running", "message": "Strategy resumed"}\n')
