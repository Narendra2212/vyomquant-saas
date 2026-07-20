const fs = require('fs');

// Read the file
const filePath = './src/App.jsx';
let content = fs.readFileSync(filePath, 'utf8');

// Search for any orphaned }, []); pattern
const orphanedPattern = /}, \[\]\);\s*\n/g;

if (orphanedPattern.test(content)) {
    console.log('Found orphaned }, []); pattern - removing all occurrences');
    
    // Remove all orphaned }, []); patterns
    const cleanedContent = content.replace(orphanedPattern, '');
    
    // Write back to file
    fs.writeFileSync(filePath, cleanedContent, 'utf8');
    console.log('Successfully removed orphaned }, []); patterns');
} else {
    console.log('No orphaned }, []); patterns found');
}
