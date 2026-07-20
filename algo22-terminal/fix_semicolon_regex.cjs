const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

// Target: the exact pattern of the broken backtest line, allowing for any whitespace
const brokenPattern = /(backtest:\s*<Backtester[^>]*setPage\("strategies"\);\s*\}\}\/>);/g;

if (brokenPattern.test(code)) {
    // Replace the matched semicolon with a comma
    code = code.replace(brokenPattern, '$1,');
    fs.writeFileSync(file, code, 'utf8');
    console.log("SUCCESS: Found and replaced the rogue semicolon with a comma.");
} else {
    console.log("FAILED: Regex did not match. Please check file manually.");
}
