const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Fixing semicolon in PAGES object...');

// Simple approach: find and replace the exact problematic pattern
const backtestPattern = /backtest:\s*<Backtester[^>]*setPage\("strategies"\);\s*\}\}\/>);/;

if (backtestPattern.test(code)) {
    // Replace the problematic semicolon with a comma
    code = code.replace(backtestPattern, 'backtest:    <Backtester strategy={activeBacktestStrategy} onBack={() => { setResumeBuilderStrategy(activeBacktestStrategy); setPage("strategies"); }}/>,');
    console.log('SUCCESS: Fixed semicolon in backtest line');
} else {
    console.log('FAILED: Could not find the exact pattern');
}

// Write the fixed code back
fs.writeFileSync(file, code, 'utf8');
console.log('Fix completed');
