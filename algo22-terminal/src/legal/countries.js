/**
 * src/legal/countries.js — LEGAL-01
 *
 * The country list for the signup declaration.
 *
 * WHY A CURATED LIST RATHER THAN ALL 249 ISO ENTRIES
 * ==================================================
 * The declaration exists to enforce Terms section 3's restricted-jurisdiction
 * clause. The server holds the authoritative restricted list
 * (backend_app/core/restricted_jurisdictions.py) and refuses a restricted code
 * regardless of what this file contains, so this is purely what the dropdown
 * offers.
 *
 * A full ISO table would be 249 entries to maintain for no benefit: the
 * declaration is UNVERIFIED either way, so a user intent on misdeclaring picks a
 * different country from whatever list is shown. What matters is that the list is
 * long enough not to force an honest user into a wrong answer, and that it
 * includes "somewhere else" so nobody is pushed into a false declaration simply
 * because their country is missing.
 *
 * THE RESTRICTED COUNTRIES ARE DELIBERATELY PRESENT IN THIS LIST.
 * Omitting them would be worse: a user in Iran would pick something else,
 * producing a false declaration and no refusal. Offering them means the honest
 * answer is available and the server refuses it with a reason the user can read.
 */

/** India first, since it is the base currency and primary market. */
export const COUNTRIES = [
  { code: 'IN', name: 'India' },

  { code: 'AE', name: 'United Arab Emirates' },
  { code: 'AR', name: 'Argentina' },
  { code: 'AT', name: 'Austria' },
  { code: 'AU', name: 'Australia' },
  { code: 'BD', name: 'Bangladesh' },
  { code: 'BE', name: 'Belgium' },
  { code: 'BR', name: 'Brazil' },
  { code: 'BY', name: 'Belarus' },
  { code: 'CA', name: 'Canada' },
  { code: 'CH', name: 'Switzerland' },
  { code: 'CL', name: 'Chile' },
  { code: 'CN', name: 'China' },
  { code: 'CO', name: 'Colombia' },
  { code: 'CU', name: 'Cuba' },
  { code: 'CZ', name: 'Czechia' },
  { code: 'DE', name: 'Germany' },
  { code: 'DK', name: 'Denmark' },
  { code: 'EG', name: 'Egypt' },
  { code: 'ES', name: 'Spain' },
  { code: 'FI', name: 'Finland' },
  { code: 'FR', name: 'France' },
  { code: 'GB', name: 'United Kingdom' },
  { code: 'GR', name: 'Greece' },
  { code: 'HK', name: 'Hong Kong SAR China' },
  { code: 'HU', name: 'Hungary' },
  { code: 'ID', name: 'Indonesia' },
  { code: 'IE', name: 'Ireland' },
  { code: 'IL', name: 'Israel' },
  { code: 'IR', name: 'Iran' },
  { code: 'IT', name: 'Italy' },
  { code: 'JP', name: 'Japan' },
  { code: 'KE', name: 'Kenya' },
  { code: 'KP', name: 'North Korea' },
  { code: 'KR', name: 'South Korea' },
  { code: 'LK', name: 'Sri Lanka' },
  { code: 'MM', name: 'Myanmar' },
  { code: 'MX', name: 'Mexico' },
  { code: 'MY', name: 'Malaysia' },
  { code: 'NG', name: 'Nigeria' },
  { code: 'NL', name: 'Netherlands' },
  { code: 'NO', name: 'Norway' },
  { code: 'NP', name: 'Nepal' },
  { code: 'NZ', name: 'New Zealand' },
  { code: 'PH', name: 'Philippines' },
  { code: 'PK', name: 'Pakistan' },
  { code: 'PL', name: 'Poland' },
  { code: 'PT', name: 'Portugal' },
  { code: 'QA', name: 'Qatar' },
  { code: 'RO', name: 'Romania' },
  { code: 'RU', name: 'Russia' },
  { code: 'SA', name: 'Saudi Arabia' },
  { code: 'SE', name: 'Sweden' },
  { code: 'SG', name: 'Singapore' },
  { code: 'SY', name: 'Syria' },
  { code: 'TH', name: 'Thailand' },
  { code: 'TR', name: 'Türkiye' },
  { code: 'TW', name: 'Taiwan' },
  { code: 'UA', name: 'Ukraine' },
  { code: 'US', name: 'United States' },
  { code: 'VE', name: 'Venezuela' },
  { code: 'VN', name: 'Vietnam' },
  { code: 'ZA', name: 'South Africa' },

  // Present so nobody is forced into a false declaration because their country
  // is not listed. The server treats it as a well-formed, non-restricted code.
  { code: 'ZZ', name: 'Somewhere else' },
];

export default COUNTRIES;
