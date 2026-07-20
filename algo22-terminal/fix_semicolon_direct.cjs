const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Direct semicolon fix...');

// Look for exact problematic pattern and fix it
if (code.includes('setPage("strategies")); }}/>;')) {
    code = code.replace('setPage("strategies")); }}/>;', 'setPage("strategies")); }/>,');
    console.log('SUCCESS: Fixed semicolon in PAGES object');
} else {
    console.log('FAILED: Could not find exact pattern');
}

// Write fixed code back
fs.writeFileSync(file, code, 'utf8');
console.log('Fix completed');
