const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Applying direct fix...');

// Direct string replacement - look for the exact problematic pattern
const problematicPattern = 'setPage("strategies")); }}/>;';
const correctPattern = 'setPage("strategies")); }/>,';

if (code.includes(problematicPattern)) {
    code = code.replace(problematicPattern, correctPattern);
    fs.writeFileSync(file, code, 'utf8');
    console.log('SUCCESS: Fixed semicolon in PAGES object');
} else {
    console.log('FAILED: Could not find the exact problematic pattern');
    // Let's try a broader search for semicolon issues
    const semicolonPattern = /;\s*$/gm;
    const lines = code.split('\n');
    let foundIssue = false;
    
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].trim();
        if (line.includes('backtest:') && line.includes('setPage("strategies")')) {
            if (line.endsWith('};')) {
                lines[i] = line.replace('};', '},');
                foundIssue = true;
                console.log(`Fixed line ${i + 1}: ${lines[i]}`);
                break;
            }
        }
    }
    
    if (foundIssue) {
        const fixedCode = lines.join('\n');
        fs.writeFileSync(file, fixedCode, 'utf8');
        console.log('SUCCESS: Fixed semicolon issue in PAGES object');
    } else {
        console.log('FAILED: Could not locate the semicolon issue');
    }
}
