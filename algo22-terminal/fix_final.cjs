const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Final semicolon fix attempt...');

// Direct string replacement - look for the exact problematic pattern
if (code.includes('setPage("strategies")); }}/>;')) {
    code = code.replace('setPage("strategies")); }}/>;', 'setPage("strategies")); }/>,');
    console.log('SUCCESS: Fixed semicolon in PAGES object');
} else {
    console.log('FAILED: Could not find the exact pattern');
}

// Write the fixed code back
fs.writeFileSync(file, code, 'utf8');
console.log('Fix completed');
