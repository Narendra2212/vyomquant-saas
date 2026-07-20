const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Fix line 424: Remove orphaned }, []); 
content = content.replace(/}, \[\]\);\s*\n/g, '');

// Fix line 432: Fix malformed className with double equals signs
content = content.replace(/className="text-\[9px\] font-mono"/g, 'className="text-[9px] font-mono"');
content = content.replace(/className=\{`text-\[9px\] font-mono font-bold flex items-center gap-0\.5 \${k\.ch>0\?"text-green-400":"text-red-400"}`\}/g, 'className={`text-[9px] font-mono font-bold flex items-center gap-0.5 ${k.ch>0?"text-green-400":"text-red-400"}`}');

// Write the fixed content back
fs.writeFileSync(filePath, content, 'utf8');
console.log('Fixed syntax errors at lines 424 and 432');
