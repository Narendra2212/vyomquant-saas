const fs = require('fs');
const file = 'algo22-terminal/src/App.jsx';
let code = fs.readFileSync(file, 'utf8');

// Find the first App function (the old one that needs to be removed)
const firstAppIndex = code.indexOf('function App() {');
if (firstAppIndex === -1) {
    console.log("ERROR: Could not find any App function");
    return;
}

// Find the second App function (the correct one to keep)
const secondAppIndex = code.indexOf('function App() {', firstAppIndex + 1);
if (secondAppIndex === -1) {
    console.log("ERROR: Could not find second App function");
    return;
}

// Find the end of the first App function by looking for the next function definition
let endOfFirstApp = code.indexOf('function', firstAppIndex + 1);
if (endOfFirstApp === -1) {
    console.log("ERROR: Could not find end of first App function");
    return;
}

// Remove the first App function and its PAGES object
const beforeFirstApp = code.substring(0, firstAppIndex);
const afterFirstApp = code.substring(endOfFirstApp);

// Combine the code, keeping only the second (correct) App function
const fixedCode = beforeFirstApp + afterFirstApp;

fs.writeFileSync(file, fixedCode, 'utf8');
console.log("SUCCESS: Removed duplicate App function and PAGES object!");
