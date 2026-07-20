const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Find line 424 specifically
const lines = content.split('\n');

// Look for the problematic line around 424
for (let i = 0; i < lines.length; i++) {
    if (i >= 420 && i <= 430 && lines[i].includes('}, []);')) {
        console.log(`Found corrupted line ${i + 1}: ${lines[i]}`);
        
        // This line needs to be removed - it's an orphaned useEffect closing
        // Check what should be here instead by looking at context
        const beforeLine = content.substring(0, content.indexOf(lines[i]));
        const afterLine = content.substring(content.indexOf(lines[i]) + lines[i].length);
        
        // Remove the orphaned line
        const cleanedContent = beforeLine + afterLine;
        
        // Write back to file
        fs.writeFileSync(filePath, cleanedContent, 'utf8');
        console.log('Removed orphaned }, []); line');
        return;
    }
}

console.log('Could not find the specific corrupted line 424');
