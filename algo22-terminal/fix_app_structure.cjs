const fs = require('fs');

// Read the App.jsx file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Find the first App function and remove the orphaned code after it
const firstAppEnd = content.indexOf('  );\n};\n\nexport default function App() {');

if (firstAppEnd === -1) {
  console.error('Could not find the first App function end');
  process.exit(1);
}

// Keep everything up to the first App function end
const cleanContent = content.substring(0, firstAppEnd + 4);

// Write the cleaned content back
fs.writeFileSync(filePath, cleanContent, 'utf8');
console.log('App.jsx structure cleaned successfully!');
