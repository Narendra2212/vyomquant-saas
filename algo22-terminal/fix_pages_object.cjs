const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Fixing PAGES object semicolon...');

// Simple approach: find all lines with backtest and fix semicolon
const lines = code.split('\n');
let fixed = false;

for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    
    // Look for backtest line with semicolon issue
    if (line.includes('backtest:') && line.includes('setPage("strategies")')) {
        // Replace semicolon with comma
        if (line.includes('};')) {
            lines[i] = line.replace('};', '},');
            fixed = true;
            console.log(`Fixed line ${i + 1}: ${lines[i].trim()}`);
        }
    }
}

if (fixed) {
    const fixedCode = lines.join('\n');
    fs.writeFileSync(file, fixedCode, 'utf8');
    console.log('SUCCESS: Fixed PAGES object semicolon');
} else {
    console.log('FAILED: Could not find backtest line');
}
