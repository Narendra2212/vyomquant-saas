const fs = require('fs');
const file = 'src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

// The exact broken string from user's error log
const brokenString = 'setPage("strategies")); }}/>;';
const fixedString = 'setPage("strategies")); }/>,';

if (code.includes(brokenString)) {
    code = code.replace(brokenString, fixedString);
    fs.writeFileSync(file, code, 'utf8');
    console.log("SUCCESS: Replaced illegal semicolon with a comma in the PAGES object.");
} else {
    console.log("FAILED: Could not find the exact broken string. Please check manually.");
}
