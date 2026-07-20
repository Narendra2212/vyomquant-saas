import re

with open("d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/routers/strategies.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update create_strategy
data_pattern = r'''(data = \{
        "user_id": user\["id"\],
        "name": body\.get\("name", "Unnamed Strategy"\),
        "symbol": body\.get\("symbol", "BTC/USDT"\),
        "timeframe": body\.get\("timeframe", "5m"\),
        "buy_logic": body\.get\("buy_logic", \{\}\),
        "sell_logic": body\.get\("sell_logic", \{\}\),
        "risk": body\.get\("risk", \{\}\),
        "indicators": body\.get\("indicators", \[\]\),
        "ml_model_path": body\.get\("ml_model_path"\),
        "exchange_id": body\.get\("exchange_id", "binance"\),
        "status": "stopped",)
        "nodes": nodes,
        "edges": edges,
        # DAG Versioning
        "dag_version": 1,  # Initial version
        "dag_schema_version": CompiledDAG\.SCHEMA_VERSION,  # Schema compatibility
        "dag_created_at": datetime\.now\(\)\.isoformat\(\),
        "dag_updated_at": datetime\.now\(\)\.isoformat\(\),
        "dag_hash": None,  # Computed below
    \}'''

new_data_pattern = r'''\1
    }
    
    # Store DAG fields inside buy_logic as workaround for missing DB columns
    buy_logic = data["buy_logic"]
    if not isinstance(buy_logic, dict):
        buy_logic = {}
        data["buy_logic"] = buy_logic
    
    buy_logic["_nodes"] = nodes
    buy_logic["_edges"] = edges
    buy_logic["_dag_version"] = 1
    buy_logic["_dag_schema_version"] = CompiledDAG.SCHEMA_VERSION
    buy_logic["_dag_created_at"] = datetime.now().isoformat()
    buy_logic["_dag_updated_at"] = datetime.now().isoformat()
    buy_logic["_dag_hash"] = None
'''

if not re.search(data_pattern, content):
    print("Could not find data dict pattern!")
    import sys; sys.exit(1)

content = re.sub(data_pattern, new_data_pattern, content)

# 2. Compute DAG hash
hash_pattern = r'''data\["dag_hash"\] = compiled\.compute_hash\(\)'''
new_hash_pattern = r'''data["buy_logic"]["_dag_hash"] = compiled.compute_hash()'''
content = content.replace(hash_pattern, new_hash_pattern)

# 3. Update list_strategies
list_func_pattern = r'''(resp = \(
            _sb\(user\)
            \.table\("strategies"\)
            \.select\("\*"\)
            \.eq\("user_id", user\["id"\]\)
            \.order\("created_at", desc=True\)
            \.execute\(\)
        \)
        return resp\.data)'''

new_list_func = r'''resp = (
            _sb(user)
            .table("strategies")
            .select("*")
            .eq("user_id", user["id"])
            .order("created_at", desc=True)
            .execute()
        )
        
        results = resp.data
        for item in results:
            if "buy_logic" in item and isinstance(item["buy_logic"], dict):
                bl = item["buy_logic"]
                item["nodes"] = bl.pop("_nodes", [])
                item["edges"] = bl.pop("_edges", [])
                item["dag_version"] = bl.pop("_dag_version", 1)
                item["dag_schema_version"] = bl.pop("_dag_schema_version", None)
                item["dag_created_at"] = bl.pop("_dag_created_at", None)
                item["dag_updated_at"] = bl.pop("_dag_updated_at", None)
                item["dag_hash"] = bl.pop("_dag_hash", None)
                item["buy_logic"] = bl
        return results'''
        
content = re.sub(list_func_pattern, new_list_func, content)

# 4. Update get_strategy
get_func_pattern = r'''(resp = \(
            _sb\(user\)
            \.table\("strategies"\)
            \.select\("\*"\)
            \.eq\("id", strategy_id\)
            \.eq\("user_id", user\["id"\]\)
            \.execute\(\)
        \)
        if not resp\.data:
            raise HTTPException\(404, "Strategy not found\."\)
        return resp\.data\[0\])'''

new_get_func = r'''resp = (
            _sb(user)
            .table("strategies")
            .select("*")
            .eq("id", strategy_id)
            .eq("user_id", user["id"])
            .execute()
        )
        if not resp.data:
            raise HTTPException(404, "Strategy not found.")
        
        item = resp.data[0]
        if "buy_logic" in item and isinstance(item["buy_logic"], dict):
            bl = item["buy_logic"]
            item["nodes"] = bl.pop("_nodes", [])
            item["edges"] = bl.pop("_edges", [])
            item["dag_version"] = bl.pop("_dag_version", 1)
            item["dag_schema_version"] = bl.pop("_dag_schema_version", None)
            item["dag_created_at"] = bl.pop("_dag_created_at", None)
            item["dag_updated_at"] = bl.pop("_dag_updated_at", None)
            item["dag_hash"] = bl.pop("_dag_hash", None)
            item["buy_logic"] = bl
        return item'''

content = re.sub(get_func_pattern, new_get_func, content)

# 5. Update deploy_bot (it fetches strategy into 'blueprint')
deploy_func_pattern = r'''(resp = \(
        _sb\(user\)
        \.table\("strategies"\)
        \.select\("\*"\)
        \.eq\("id", strategy_id\)
        \.eq\("user_id", user\["id"\]\)
        \.execute\(\)
    \)
    if not resp\.data:
        logger\.error\(f"\[STRATEGIES\] Strategy not found for deploy: \{strategy_id\}"\)
        raise HTTPException\(404, "Strategy not found\."\)

    blueprint = resp\.data\[0\])'''

new_deploy_func = r'''resp = (
        _sb(user)
        .table("strategies")
        .select("*")
        .eq("id", strategy_id)
        .eq("user_id", user["id"])
        .execute()
    )
    if not resp.data:
        logger.error(f"[STRATEGIES] Strategy not found for deploy: {strategy_id}")
        raise HTTPException(404, "Strategy not found.")

    blueprint = resp.data[0]
    if "buy_logic" in blueprint and isinstance(blueprint["buy_logic"], dict):
        bl = blueprint["buy_logic"]
        blueprint["nodes"] = bl.pop("_nodes", [])
        blueprint["edges"] = bl.pop("_edges", [])
        blueprint["dag_version"] = bl.pop("_dag_version", 1)
        blueprint["dag_schema_version"] = bl.pop("_dag_schema_version", None)
        blueprint["dag_created_at"] = bl.pop("_dag_created_at", None)
        blueprint["dag_updated_at"] = bl.pop("_dag_updated_at", None)
        blueprint["dag_hash"] = bl.pop("_dag_hash", None)
        blueprint["buy_logic"] = bl'''

content = re.sub(deploy_func_pattern, new_deploy_func, content)

with open("d:/aerora_quant_backend_updated_final1/aerora_quant_backend_updated_final1/routers/strategies.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Patching complete.")
