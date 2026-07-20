const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Find the corrupted line 424 with }, []);
const lines = content.split('\n');

// Look for line containing }, []); around line 424
for (let i = 0; i < lines.length; i++) {
    if (lines[i].includes('}, []);') && i >= 410 && i <= 430) {
        console.log(`Found corrupted line at index ${i}: ${lines[i]}`);
        
        // This is likely an orphaned useEffect closing
        // We need to find what should be here instead
        // Looking at the context, this should be part of a useEffect hook
        
        // Replace the corrupted line with proper useEffect structure
        // The pattern should be something like: useEffect(() => { ... }, []);
        
        // For now, let's just remove the orphaned line and see what's left
        const beforeLine = content.substring(0, content.indexOf(lines[i]));
        const afterLine = content.substring(content.indexOf(lines[i]) + lines[i].length);
        
        const cleanedContent = beforeLine + afterLine;
        
        // Write back to file
        fs.writeFileSync(filePath, cleanedContent, 'utf8');
        console.log('Removed orphaned }, []); line');
        return;
    }
}

console.log('Could not find the specific corrupted line');
