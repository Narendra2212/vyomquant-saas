/**
 * @fileoverview Portfolio Page CSS and Typography Validation
 * Static analysis test to verify Portfolio.jsx uses proper CSS values and typography tokens
 * @version 1.0.0
 */

import { describe, it, expect } from 'vitest';

describe('Portfolio Page CSS and Typography Validation', () => {
  
  it('should check that no invalid fontSize patterns exist in source', () => {
    // Read the source file content for validation
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for invalid fontSize patterns from the original code
    const invalidFontSizePatterns = [
      /fontSize:\s*9\b/,   // fontSize: 9 (no units)
      /fontSize:\s*20\b/,  // fontSize: 20 (no units)
      /fontSize:\s*12\b/,  // fontSize: 12 (no units)
      /fontSize:\s*10\b/,  // fontSize: 10 (no units)
      /fontSize:\s*8\b/,   // fontSize: 8 (no units)
    ];
    
    invalidFontSizePatterns.forEach(pattern => {
      const matches = portfolioContent.match(pattern);
      expect(matches).toBeNull();
      if (matches) {
        console.error(`Found invalid fontSize pattern: ${pattern}`);
      }
    });
  });

  it('should verify Tailwind typography classes are used', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for proper Tailwind typography classes
    const expectedClasses = [
      'text-heading-lg',
      'text-caption-sm', 
      'text-body',
      'text-caption',
      'text-micro',
    ];
    
    expectedClasses.forEach(className => {
      expect(portfolioContent).toContain(className);
    });
  });

  it('should verify no invalid unitless spacing values', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for invalid spacing patterns from the original code
    const invalidSpacingPatterns = [
      /padding:\s*20\b/,    // padding: 20 (no units)
      /gap:\s*10\b/,         // gap: 10 (no units)
      /marginBottom:\s*16\b/, // marginBottom: 16 (no units)
      /gap:\s*12\b/,         // gap: 12 (no units)
      /marginBottom:\s*12\b/, // marginBottom: 12 (no units)
      /gap:\s*5\b/,          // gap: 5 (no units)
      /gap:\s*8\b/,          // gap: 8 (no units)
    ];
    
    invalidSpacingPatterns.forEach(pattern => {
      const matches = portfolioContent.match(pattern);
      expect(matches).toBeNull();
      if (matches) {
        console.error(`Found invalid spacing pattern: ${pattern}`);
      }
    });
  });

  it('should verify proper CSS units in inline styles', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check that spacing values have proper units
    const validSpacingPatterns = [
      /padding:\s*["']?\d+px["']?/,  // padding: "20px" or padding: 20px
      /gap:\s*["']?\d+px["']?/,       // gap: "10px" or gap: 10px
      /marginBottom:\s*["']?\d+px["']?/, // marginBottom: "16px" or marginBottom: 16px
    ];
    
    // At least some spacing should have proper units
    const hasValidSpacing = validSpacingPatterns.some(pattern => 
      portfolioContent.match(pattern)
    );
    expect(hasValidSpacing).toBe(true);
  });

  it('should verify semantic color tokens are used', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for semantic color tokens
    expect(portfolioContent).toContain('#10B981'); // accent-profit
    expect(portfolioContent).toContain('#EF4444'); // accent-loss
  });

  it('should verify semantic HTML structure', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for semantic ul/li elements
    expect(portfolioContent).toContain('<ul');
    expect(portfolioContent).toContain('role="list"');
    expect(portfolioContent).toContain('<li');
    expect(portfolioContent).toContain('role="listitem"');
  });

  it('should verify loading states with Activity spinner', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for loading indicator components
    expect(portfolioContent).toContain('Activity');
    expect(portfolioContent).toContain('animate-spin');
  });

  it('should verify improved empty states', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for better empty state messaging
    expect(portfolioContent).toMatch(/No.*data available/i);
    expect(portfolioContent).toMatch(/Connect.*exchange/i);
    expect(portfolioContent).toMatch(/Open positions/i);
    expect(portfolioContent).toMatch(/Trade history/i);
  });

  it('should verify monospace font usage for numeric values', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check that numeric values use monospace font
    const monospacePattern = /fontFamily:\s*["']?monospace["']?/g;
    const monospaceMatches = portfolioContent.match(monospacePattern);
    expect(monospaceMatches).toBeTruthy();
    expect(monospaceMatches.length).toBeGreaterThan(2); // Should have multiple instances
  });

  it('should verify trending icons for P&L indicators', () => {
    const fs = require('fs');
    const path = require('path');
    const portfolioFilePath = path.resolve(__dirname, '../../src/pages/Portfolio.jsx');
    const portfolioContent = fs.readFileSync(portfolioFilePath, 'utf-8');
    
    // Check for TrendingUp and TrendingDown icons
    expect(portfolioContent).toContain('TrendingUp');
    expect(portfolioContent).toContain('TrendingDown');
  });
});