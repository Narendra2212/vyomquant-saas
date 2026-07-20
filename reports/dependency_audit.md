# Dependency Audit Report

**Generated:** 2026-06-08  
**Tools:** pip-audit (Python), npm audit (Node/JavaScript)

---

## Python Dependencies (pip-audit)

**Status:** INCOMPLETE — pip-audit failed to resolve dependencies.  
**Reason:** `numba` in `requirements.txt` is incompatible with Python 3.14 (requires <=3.13). pip-audit's isolated venv creation failed during wheel build.

**Requirements file:** `requirements.txt`  
**Recommendation:** Run `pip-audit -r requirements.txt` in a Python 3.11 or 3.12 environment to get full results.

---

## Node.js Dependencies (npm audit)

**Directory:** `algo22-terminal/`  
**Packages audited:** 434  

### Vulnerability Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High     | 3 |
| Moderate | 1 |
| Low      | 0 |
| **Total** | **4** |

---

### HIGH — react-router

| Field | Value |
|-------|-------|
| Package | `react-router` |
| Severity | HIGH |
| Vulnerable Range | `7.0.0 – 7.14.2` |
| Advisory | https://github.com/advisories/GHSA-8x6r-g9mw-2r78 |
| Fix Available | Yes (upgrade) |
| Via | `react-router-dom` |

### HIGH — react-router-dom

| Field | Value |
|-------|-------|
| Package | `react-router-dom` |
| Severity | HIGH |
| Vulnerable Range | `7.0.0-pre.0 – 7.14.2` |
| Fix Available | Yes (upgrade) |

### HIGH — axios

| Field | Value |
|-------|-------|
| Package | `axios` |
| Severity | HIGH |
| Vulnerable Range | `1.0.0 – 1.15.2` |
| Advisory | https://github.com/advisories/GHSA-pjwm-pj3p-43mv |
| Fix Available | Yes (upgrade) |
| Installed Version | `^1.15.0` (per package.json) |

### MODERATE — ws

| Field | Value |
|-------|-------|
| Package | `ws` |
| Severity | MODERATE |
| Vulnerable Range | `8.0.0 – 8.20.0` |
| Advisory | https://github.com/advisories/GHSA-58qx-3vcg-4xpx |
| Fix Available | Yes (upgrade) |

---

## Remediation Commands

```bash
# Node.js — fix all auto-fixable
cd algo22-terminal
npm audit fix

# Force upgrade (may include breaking changes)
npm audit fix --force
```
