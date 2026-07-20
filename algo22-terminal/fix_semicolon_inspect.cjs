const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Inspecting file for semicolon issues...');

// Split into lines and look for the problematic pattern
const lines = code.split('\n');
let fixed = false;

for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    
    // Look for backtest line with semicolon issue
    if (line.includes('backtest:') && line.includes('setPage("strategies"));')) {
        console.log(`Found problematic line ${i + 1}: ${line}`);
        
        // Check if it ends with semicolon instead of comma
        if (line.endsWith('};')) {
            lines[i] = line.replace('};', '},');
            fixed = true;
            console.log(`Fixed line ${i + 1} to: ${lines[i]}`);
            break;
        }
    }
}

if (fixed) {
    const fixedCode = lines.join('\n');
    fs.writeFileSync(file, fixedCode, 'utf8');
    console.log("SUCCESS: Fixed semicolon in PAGES object.");
} else {
    console.log("FAILED: Could not find the problematic line.");
}
