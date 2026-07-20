const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

console.log('Performing comprehensive fix...');

// Read the current file and analyze structure
const lines = code.split('\n');

// Find the PAGES object and fix any semicolon issues
let pagesStartIndex = -1;
let pagesEndIndex = -1;

for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (line.startsWith('const PAGES = {')) {
        pagesStartIndex = i;
        console.log(`Found PAGES object at line ${i + 1}`);
        
        // Find the end of PAGES object
        let braceCount = 0;
        for (let j = i; j < lines.length; j++) {
            const currentLine = lines[j].trim();
            if (currentLine === 'const PAGES = {') {
                braceCount++;
            } else if (currentLine === '};') {
                braceCount--;
                if (braceCount === 0) {
                    pagesEndIndex = j;
                    console.log(`Found PAGES object end at line ${j + 1}`);
                    break;
                }
            }
        }
        
        if (pagesEndIndex !== -1) {
            // Extract the PAGES object content
            const pagesContent = lines.slice(i, pagesEndIndex + 1).join('\n');
            
            // Fix semicolon issues in PAGES object
            const fixedPagesContent = pagesContent
                .replace(/;\s*$/gm, ',')  // Replace semicolons with commas
                .replace(/setPage\("strategies"\);\s*\}\}\/>);/g, 'setPage("strategies"); }/>,');  // Fix the specific backtest line
                .replace(/;\s*$/gm, ',');  // Ensure all lines end with commas except the last one
            
            // Rebuild the code with fixed PAGES object
            const beforePages = lines.slice(0, i);
            const afterPages = lines.slice(pagesEndIndex + 1);
            const fixedCode = [...beforePages, ...fixedPagesContent.split('\n'), ...afterPages].join('\n');
            
            fs.writeFileSync(file, fixedCode, 'utf8');
            console.log("SUCCESS: Fixed PAGES object semicolon issues.");
            break;
        }
    }
}

if (pagesStartIndex === -1) {
    console.log("FAILED: Could not find PAGES object.");
}
