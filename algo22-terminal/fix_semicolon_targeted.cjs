const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Targeting specific semicolon issue...');

// Look for the exact backtest line with semicolon issue
const lines = code.split('\n');
let fixed = false;

for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Check if this line contains backtest and ends with semicolon instead of comma
    if (line.includes('backtest:') && line.includes('setPage("strategies")); }}/>;')) {
        // Replace the semicolon with a comma
        lines[i] = line.replace('setPage("strategies")); }}/>;', 'setPage("strategies")); }/>,');
        fixed = true;
        console.log(`Fixed line ${i + 1}: ${lines[i]}`);
        break;
    }
}

if (fixed) {
    const fixedCode = lines.join('\n');
    fs.writeFileSync(file, fixedCode, 'utf8');
    console.log("SUCCESS: Fixed the semicolon in PAGES object.");
} else {
    console.log("FAILED: Could not locate the problematic line.");
}
