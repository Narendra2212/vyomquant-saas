const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Find the empty line 424 and add the missing useEffect opening
const lines = content.split('\n');

for (let i = 0; i < lines.length; i++) {
    if (i === 423 && lines[i].trim() === '') {
        // This is the empty line 424, we need to add the missing useEffect opening
        // Insert the missing useEffect(() => { after line 423
        lines[i] = '  useEffect(() => {';
        
        // Reconstruct the content
        const fixedContent = lines.join('\n');
        
        // Write back to file
        fs.writeFileSync(filePath, fixedContent, 'utf8');
        console.log('Added missing useEffect(() => { at line 424');
        return;
    }
}

console.log('Could not find the empty line 424 to fix');
