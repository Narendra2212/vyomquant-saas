const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Find the problematic block
const problematicStart = '// Send update to backend';
const problematicEnd = '});\n    };\n\n  // Channel data for UI';

const startIndex = content.indexOf(problematicStart);
const endIndex = content.indexOf(problematicEnd);

if (startIndex !== -1 && endIndex !== -1) {
    // Remove the problematic block
    const beforeBlock = content.substring(0, startIndex);
    const afterBlock = content.substring(endIndex + problematicEnd.length);
    
    const cleanedContent = beforeBlock + afterBlock;
    
    // Write back to file
    fs.writeFileSync(filePath, cleanedContent, 'utf8');
    console.log('Successfully removed corrupted await api.request block');
} else {
    console.log('Could not find the problematic block');
    console.log('Start index:', startIndex);
    console.log('End index:', endIndex);
}
