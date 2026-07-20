const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Fixing semicolon in PAGES object...');

// Look for the specific pattern: backtest line ending with ; instead of ,
const backtestPattern = /backtest:\s*<Backtester[^>]*>\s*;\s*portfolio:/;
if (backtestPattern.test(code)) {
  code = code.replace(backtestPattern, 'backtest:    <Backtester strategy={activeBacktestStrategy} onBack={() => { setResumeBuilderStrategy(activeBacktestStrategy); setPage("strategies"); }}/>,\n    portfolio:');
  console.log('Fixed semicolon in backtest line');
}

// Also check for any other semicolons in the PAGES object
const pagesObjectPattern = /const PAGES = \{([\s\S]*?)\};/;
const pagesMatch = code.match(pagesObjectPattern);
if (pagesMatch) {
  let pagesContent = pagesMatch[1];
  // Replace semicolons with commas within the object
  pagesContent = pagesContent.replace(/;\s*$/gm, ',');
  code = code.replace(pagesObjectMatch[0], `const PAGES = {${pagesContent}};`);
  console.log('Fixed other semicolons in PAGES object');
}

// Write the fixed code back
fs.writeFileSync(file, code, 'utf8');
console.log("SUCCESS: Fixed semicolon issues in PAGES object");
