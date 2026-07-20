const fs = require('fs');

// Read the App.jsx file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

console.log('Original file length:', content.length);

// Fix 1: Fix the squashed line
content = content.replace('}, [go]);if (page === "landing")', '}, [go]);\n\n  if (page === "landing")');

// Fix 2: Find and remove the stray bracket before useEffect
const addEventListenerIndex = content.indexOf("window.addEventListener('navigate', handleNavigate);");
if (addEventListenerIndex === -1) {
  console.error('Could not find window.addEventListener line');
  process.exit(1);
}

// Look for the stray bracket before the useEffect
const beforeAddEventListener = content.substring(0, addEventListenerIndex);
const strayBracketIndex = beforeAddEventListener.lastIndexOf('}\n');

if (strayBracketIndex === -1) {
  console.error('Could not find stray bracket');
  process.exit(1);
}

// Check if there's a useEffect opening after the stray bracket
const afterStrayBracket = content.substring(strayBracketIndex + 2, addEventListenerIndex);
const useIndex = afterStrayBracket.indexOf('useEffect');

if (useIndex === -1) {
  console.error('Could not find useEffect after stray bracket');
  process.exit(1);
}

// Remove the stray bracket
const beforeStrayBracket = content.substring(0, strayBracketIndex);
const afterStrayBracketContent = content.substring(strayBracketIndex + 2);

// Rebuild content without stray bracket
content = beforeStrayBracket + afterStrayBracketContent;

// Fix 3: Ensure useEffect is properly formatted
const useStartIndex = content.indexOf('useEffect');
if (useStartIndex === -1) {
  console.error('Could not find useEffect');
  process.exit(1);
}

// Make sure useEffect opens correctly
const beforeUseEffect = content.substring(0, useStartIndex);
const afterUseEffect = content.substring(useStartIndex);

// Replace useEffect with proper formatting
content = beforeUseEffect + '  useEffect(() => {' + afterUseEffect.substring('useEffect'.length);

// Fix 4: Verify end of file structure
const exportIndex = content.lastIndexOf('export default function App() {');
if (exportIndex === -1) {
  console.error('Could not find export default function App');
  process.exit(1);
}

// Check if there's a proper closing before export
const beforeExport = content.substring(0, exportIndex);
const lastBraceIndex = beforeExport.lastIndexOf('}');

if (lastBraceIndex === -1) {
  console.error('Could not find closing brace before export');
  process.exit(1);
}

// Write the fixed content back
fs.writeFileSync(filePath, content, 'utf8');
console.log('App component scope repaired successfully!');
console.log('Fixed file length:', content.length);
