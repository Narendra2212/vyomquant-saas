import re

def main():
    with open('algo22-terminal/src/App.jsx', 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Update handleSaveStrategy
    save_pattern = r'(const handleSaveStrategy = async \(\) => {\n\s*if \(isSavingStrategy\) return;\n\s*setIsSavingStrategy\(true\);\n\s*setSaveState\(""\);\n\s*try {)'
    save_replace = r'''\1
      const isValid = await handleValidateStrategy();
      if (!isValid) {
        setIsSavingStrategy(false);
        return;
      }
'''
    content = re.sub(save_pattern, save_replace, content)

    # 2. Update handleDeployLive
    deploy_live_pattern = r'(const handleDeployLive = async \(\) => {\n\s*if \(isSavingStrategy\) return;\n\n\s*// Validate before deploying)'
    deploy_live_replace = r'''const handleDeployLive = async () => {
    if (isSavingStrategy) return;

    const isValid = await handleValidateStrategy();
    if (!isValid) return;

    // Validate before deploying'''
    content = re.sub(deploy_live_pattern, deploy_live_replace, content)

    # 3. Add selectedDeployExchange state
    state_pattern = r'(const \[pipelineError, setPipelineError\] = useState\(null\);)'
    state_replace = r'''\1
  const [selectedDeployExchange, setSelectedDeployExchange] = useState("binance");'''
    content = re.sub(state_pattern, state_replace, content)

    # 4. Modify handleDeployLive to use selectedDeployExchange
    deploy_exchange_pattern = r'await endpoints\.strategies\.deploy\(currentId, \{ exchange_id: "binance" \}\);'
    deploy_exchange_replace = r'await endpoints.strategies.deploy(currentId, { exchange_id: selectedDeployExchange || "binance" });'
    content = re.sub(deploy_exchange_pattern, deploy_exchange_replace, content)

    # 5. Add exchange dropdown next to 'Deploy Live'
    dropdown_pattern = r'(<Btn v=\{builderMode === \'backtest\' \? "primary" : "outline"\} sz="sm" Icon=\{builderMode === \'backtest\' \? Play : Radio\} onClick=\{builderMode === \'backtest\' \? handleBacktest : handleDeployLive\} disabled=\{isSavingStrategy\}>\n\s*\{builderMode === \'backtest\' \? "Backtest" : saveState === "deployed" \? "Deployed!" : "Deploy Live"\}\n\s*</Btn>)'
    dropdown_replace = r'''{builderMode !== 'backtest' && (
              <select 
                value={selectedDeployExchange} 
                onChange={(e) => setSelectedDeployExchange(e.target.value)}
                style={{ background: C.bg3, color: C.t1, border: `1px solid ${C.border}`, borderRadius: 4, padding: "0 8px", fontSize: 10, fontFamily: "monospace" }}
              >
                <option value="binance">Binance</option>
                <option value="bybit">Bybit</option>
                <option value="okx">OKX</option>
              </select>
            )}
            \1'''
    content = re.sub(dropdown_pattern, dropdown_replace, content)

    with open('algo22-terminal/src/App.jsx', 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    main()
