# Market Data Validation Layer

**Date:** May 1, 2026

---

## Overview

Built a **comprehensive market data validation layer** with multi-tier validation:

```
Raw Data → Structural → Candle Integrity → Outlier Detection → Gap Handling → Clean Data
              ↓              ↓                  ↓                ↓
         Issues Log    OHLC Fix         Smooth Outliers    Interpolate
              ↓              ↓                  ↓                ↓
         Quality Score (0-100) → DAG Execution (if score ≥ 70)
```

**Features:**
- ✅ Missing data detection and interpolation
- ✅ Outlier filtering (Z-score, IQR, Percent Change, Isolation Forest)
- ✅ Candle integrity checks (OHLC relationships, volume validation)
- ✅ Gap detection with multiple handling strategies
- ✅ Data quality scoring (0-100)
- ✅ Fallback mechanisms (alternative sources)
- ✅ DAG integration (pre-execution validation)

---

## Architecture

### Validation Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                     VALIDATION PIPELINE                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Step 1: Structural Validation                                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ • Check required columns (open, high, low, close, volume)    │   │
│  │ • Remove duplicate timestamps                               │   │
│  │ • Sort by timestamp                                          │   │
│  │ • Validate data types                                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Step 2: Candle Integrity                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ • OHLC relationships:                                        │   │
│  │   - low ≤ open ≤ high ✓                                     │   │
│  │   - low ≤ close ≤ high ✓                                    │   │
│  │   - low ≤ high ✓                                            │   │
│  │ • Volume ≥ 0                                               │   │
│  │ • Price > 0                                                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Step 3: Outlier Detection                                            │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Methods:                                                    │   │
│  │ • Z-Score: |z| > 3 → Outlier (smooth with median)          │   │
│  │ • IQR: Outside [Q1-1.5×IQR, Q3+1.5×IQR] → Outlier          │   │
│  │ • Percent Change: |Δ%| > 5% → Outlier                      │   │
│  │ • Isolation Forest: ML-based anomaly detection             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Step 4: Gap Handling                                                 │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Strategies:                                                 │   │
│  │ • Forward Fill: Persist last known values                    │   │
│  │ • Linear Interpolation: Connect gaps linearly               │   │
│  │ • Spline Interpolation: Smooth curve through gaps           │   │
│  │ • Synthetic Fill: Generate from volatility model            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  Step 5: Quality Scoring                                              │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Score (0-100):                                              │   │
│  │ • Validity ratio: 50 points                                 │   │
│  │ • Issue penalty: -5 per issue (max -30)                      │   │
│  │ • Gap penalty: -1 per 10 minutes (max -20)                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Quality Score Calculation

```
Score = ValidityRatio × 100 - IssuePenalty - GapPenalty

Where:
  ValidityRatio = ValidCandles / TotalCandles
  IssuePenalty = min(IssueCount × 5, 30)
  GapPenalty = min(MaxGapMinutes / 10, 20)

Quality Levels:
  95-100: EXCELLENT
  85-94:  GOOD
  70-84:  ACCEPTABLE ⭐ (min for DAG execution)
  50-69:  POOR
  0-49:   UNUSABLE → Trigger fallback
```

---

## Core Components

### 1. ValidationConfig

```python
@dataclass
class ValidationConfig:
    # Quality thresholds
    min_quality_score: float = 70.0
    max_gap_minutes: float = 5.0
    
    # Outlier detection
    outlier_method: OutlierMethod = OutlierMethod.Z_SCORE
    z_score_threshold: float = 3.0
    iqr_multiplier: float = 1.5
    percent_change_threshold: float = 0.05  # 5%
    
    # Gap handling
    gap_strategy: GapHandlingStrategy = GapHandlingStrategy.LINEAR_INTERPOLATE
    max_interpolation_gap_minutes: float = 30.0
    
    # Candle validation
    check_ohlc_relationships: bool = True
    check_volume_positive: bool = True
    check_prices_positive: bool = True
    
    # Fallback
    enable_fallback: bool = True
```

### 2. StructuralValidator

```python
class StructuralValidator:
    REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]
    
    @classmethod
    def validate(cls, df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """
        Validates:
        - Required columns present
        - No duplicate timestamps
        - Timestamp index
        - Sorted by time
        """
```

### 3. CandleIntegrityValidator

```python
class CandleIntegrityValidator:
    @classmethod
    def validate(cls, df: pd.DataFrame, symbol: str) -> Tuple[pd.DataFrame, List[ValidationIssue]]:
        """
        Validates & fixes:
        - OHLC relationships (low ≤ open ≤ high, etc.)
        - Volume ≥ 0
        - Price > 0
        """
```

### 4. OutlierDetector

```python
class OutlierDetector:
    Methods:
        - Z_SCORE: Statistical standard deviation method
        - IQR: Interquartile range method
        - PERCENT_CHANGE: Price change threshold
        - ISOLATION_FOREST: ML-based anomaly detection
    
    @classmethod
    def detect(cls, df, method, config) -> Tuple[pd.DataFrame, List[ValidationIssue]]
```

**Outlier Detection Methods:**

| Method | Description | When to Use |
|--------|-------------|-------------|
| **Z-Score** | `\|z\| > 3` → outlier | Normal distributions |
| **IQR** | Outside `[Q1-1.5×IQR, Q3+1.5×IQR]` | Skewed distributions |
| **Percent Change** | `\|Δ%\| > threshold` | High-volatility assets |
| **Isolation Forest** | ML-based | Complex patterns |

### 5. GapHandler

```python
class GapHandler:
    Strategies:
        - FORWARD_FILL: Persist last values
        - LINEAR_INTERPOLATE: Linear interpolation
        - SPLINE_INTERPOLATE: Cubic spline
        - SYNTHETIC_FILL: Generate from volatility model
    
    @classmethod
    def handle(cls, df, timeframe, strategy, max_gap) -> Tuple[pd.DataFrame, List[ValidationIssue]]
```

**Gap Handling Strategies:**

| Strategy | Method | Best For |
|----------|--------|----------|
| **Forward Fill** | Copy last known value | Short gaps (< 5 min) |
| **Linear Interpolate** | Straight line between points | Medium gaps (5-30 min) |
| **Spline Interpolate** | Smooth curve | Longer gaps with trend |
| **Synthetic Fill** | Random walk from volatility | Very long gaps |

### 6. MarketDataValidator (Main Engine)

```python
class MarketDataValidator:
    def __init__(self, config: ValidationConfig)
    
    # Main validation
    async def validate(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str
    ) -> Tuple[pd.DataFrame, DataQualityReport]
    
    # Fallback
    async def _attempt_fallback(symbol, timeframe) -> Optional[pd.DataFrame]
    
    # Register alternative sources
    def register_fallback_source(name, fetcher: Callable)
```

### 7. ValidatedDataFeed (DAG Integration)

```python
class ValidatedDataFeed:
    """
    Integration layer between validation and DAG execution.
    Ensures only quality data reaches the DAG.
    """
    
    def __init__(self, validator, min_quality_score=70.0)
    
    async def get_data(symbol, timeframe, raw_fetcher) -> Optional[pd.DataFrame]
    # Returns None if quality insufficient
```

---

## DAG Integration

### Pre-Execution Validation

```python
from backend.market_data_validation import (
    MarketDataValidator,
    ValidatedDataFeed,
    ValidationConfig
)
from backend.dag_event_loop import DAGEventLoop

# Create validator
config = ValidationConfig(
    min_quality_score=75.0,
    outlier_method=OutlierMethod.Z_SCORE,
    gap_strategy=GapHandlingStrategy.LINEAR_INTERPOLATE
)

validator = MarketDataValidator(config)

# Create validated feed
validated_feed = ValidatedDataFeed(validator, min_quality_score=75.0)

# In DAG event loop
class ValidatedDAGEventLoop(DAGEventLoop):
    def __init__(self, validated_feed, ...):
        super().__init__(...)
        self.validated_feed = validated_feed
    
    async def _process_event(self, event):
        # Get validated data instead of raw
        df = await self.validated_feed.get_data(
            symbol=event.symbol,
            timeframe=self.timeframe,
            raw_fetcher=lambda: self.fetch_raw_data(event.symbol)
        )
        
        if df is None:
            # Quality too low - skip execution
            report = self.validated_feed.get_quality_report(
                event.symbol, self.timeframe
            )
            logger.warning(
                f"Skipping DAG execution for {event.symbol}: "
                f"quality={report.quality_score:.1f} < 75"
            )
            return
        
        # Data is clean - proceed with DAG execution
        result = self.execute_dag(df)
```

### Validation Callbacks

```python
# Get real-time quality reports
async def on_validation_report(report: DataQualityReport):
    if report.quality_level == DataQualityLevel.UNUSABLE:
        await send_alert(
            f"Data quality critical for {report.symbol}: "
            f"{report.quality_score:.1f}/100"
        )
    
    # Log to monitoring
    metrics.record("data_quality", report.quality_score, 
                   tags={"symbol": report.symbol})

validator.add_validation_callback(on_validation_report)
```

---

## API Endpoints

### Validate Data

```http
POST /api/market/validation/validate
Content-Type: application/json

{
  "symbol": "BTCUSDT",
  "timeframe": "1m",
  "min_quality_score": 75.0
}

Response:
{
  "symbol": "BTCUSDT",
  "timeframe": "1m",
  "quality_score": 87.5,
  "quality_level": "good",
  "total_candles": 100,
  "valid_candles": 98,
  "issues_found": 2,
  "actions_taken": ["linear_interpolation"]
}
```

### Get Validation Report

```http
GET /api/market/validation/report/BTCUSDT?timeframe=1m

Response:
{
  "symbol": "BTCUSDT",
  "timeframe": "1m",
  "total_candles": 100,
  "valid_candles": 98,
  "invalid_candles": 2,
  "missing_values": 3,
  "duplicate_timestamps": 0,
  "ohlc_violations": 1,
  "outliers_detected": 4,
  "gaps_found": 1,
  "max_gap_duration_minutes": 2.5,
  "quality_score": 87.5,
  "quality_level": "good",
  "issues": [
    {
      "issue_type": "ohlc_violations",
      "severity": "warning",
      "message": "Fixed 1 OHLC violations"
    },
    {
      "issue_type": "data_gaps",
      "severity": "warning",
      "message": "Found 1 gaps, max: 2.5 minutes"
    }
  ],
  "actions_taken": ["linear_interpolation"]
}
```

### Get Validation Statistics

```http
GET /api/market/validation/stats

Response:
{
  "validation_count": 1523,
  "total_issues_found": 2847,
  "fallback_sources": 2
}
```

### Update Configuration

```http
POST /api/market/validation/config
Content-Type: application/json

{
  "min_quality_score": 80.0,
  "outlier_method": "z_score",
  "z_score_threshold": 2.5,
  "gap_strategy": "spline_interpolate",
  "check_ohlc_relationships": true,
  "enable_fallback": true
}

Response:
{
  "status": "updated",
  "config": {
    "min_quality_score": 80.0,
    "outlier_method": "z_score",
    "gap_strategy": "spline_interpolate"
  }
}
```

---

## Usage Examples

### Example 1: Basic Validation

```python
from backend.market_data_validation import (
    MarketDataValidator,
    ValidationConfig,
    GapHandlingStrategy,
    OutlierMethod
)

# Configure validator
config = ValidationConfig(
    min_quality_score=70.0,
    outlier_method=OutlierMethod.Z_SCORE,
    z_score_threshold=3.0,
    gap_strategy=GapHandlingStrategy.LINEAR_INTERPOLATE,
    max_gap_minutes=5.0
)

validator = MarketDataValidator(config)

# Validate data
df = fetch_market_data("BTCUSDT", "1m")
clean_df, report = await validator.validate(df, "BTCUSDT", "1m")

print(f"Quality Score: {report.quality_score:.1f}/100")
print(f"Quality Level: {report.quality_level.value}")
print(f"Issues Found: {len(report.issues)}")

for issue in report.issues:
    print(f"  - {issue.issue_type}: {issue.message}")

# Use if quality sufficient
if report.quality_score >= 70:
    result = dag_engine.execute_dag(clean_df)
else:
    print("Data quality insufficient - skipping execution")
```

### Example 2: Handling Gaps

```python
from backend.market_data_validation import GapHandler, GapHandlingStrategy

# Detect gaps
df_with_gaps = pd.DataFrame(...)

# Strategy 1: Forward fill (best for short gaps)
df_filled = GapHandler._forward_fill(df_with_gaps, "1m")

# Strategy 2: Linear interpolation (smooth transitions)
df_interpolated = GapHandler._interpolate(
    df_with_gaps, "1m", method="linear"
)

# Strategy 3: Synthetic fill (realistic market simulation)
df_synthetic = GapHandler._synthetic_fill(df_with_gaps, "1m")

# Auto-handle with validator
clean_df, report = await validator.validate(df_with_gaps, "BTC", "1m")
# Uses configured gap_strategy automatically
```

### Example 3: Outlier Detection

```python
from backend.market_data_validation import OutlierDetector, OutlierMethod

# Method 1: Z-Score (statistical outliers)
df_clean, _ = OutlierDetector.detect(
    df_raw,
    method=OutlierMethod.Z_SCORE,
    config=ValidationConfig(z_score_threshold=3.0)
)

# Method 2: IQR (robust to extreme values)
df_clean, _ = OutlierDetector.detect(
    df_raw,
    method=OutlierMethod.IQR,
    config=ValidationConfig(iqr_multiplier=1.5)
)

# Method 3: Percent Change (flash crash detection)
df_clean, _ = OutlierDetector.detect(
    df_raw,
    method=OutlierMethod.PERCENT_CHANGE,
    config=ValidationConfig(percent_change_threshold=0.10)  # 10%
)
```

### Example 4: Fallback Sources

```python
# Register fallback data sources
async def fetch_from_binance(symbol, timeframe):
    return await binance_client.get_klines(symbol, timeframe)

async def fetch_from_coinbase(symbol, timeframe):
    return await coinbase_client.get_candles(symbol, timeframe)

async def fetch_from_database(symbol, timeframe):
    return await db.get_historical_data(symbol, timeframe)

# Register sources (priority order)
validator.register_fallback_source("binance", fetch_from_binance)
validator.register_fallback_source("coinbase", fetch_from_coinbase)
validator.register_fallback_source("database", fetch_from_database)

# Validate with fallback
df_primary = await fetch_from_primary_exchange("BTCUSDT", "1m")
clean_df, report = await validator.validate(df_primary, "BTCUSDT", "1m")

# If quality < threshold, auto-tries:
# 1. binance → 2. coinbase → 3. database
```

### Example 5: Real-Time Quality Monitoring

```python
async def quality_monitor():
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
    
    while True:
        for symbol in symbols:
            df = await fetch_latest_data(symbol)
            clean_df, report = await validator.validate(df, symbol, "1m")
            
            # Log quality metrics
            metrics.gauge(
                "data_quality_score",
                report.quality_score,
                tags={"symbol": symbol}
            )
            
            # Alert on degradation
            if report.quality_level == DataQualityLevel.POOR:
                await send_alert(
                    f"Data quality degraded for {symbol}: "
                    f"{report.quality_score:.0f}/100"
                )
            
            # Alert on critical issues
            if report.quality_level == DataQualityLevel.UNUSABLE:
                await send_alert(
                    f"CRITICAL: Data unusable for {symbol}. "
                    f"DAG execution suspended."
                )
        
        await asyncio.sleep(60)

# Start monitoring
asyncio.create_task(quality_monitor())
```

### Example 6: Complete DAG Integration

```python
from backend.market_data_validation import (
    MarketDataValidator,
    ValidatedDataFeed,
    ValidationConfig,
    GapHandlingStrategy,
    OutlierMethod
)
from backend.dag_event_loop import DAGEventLoop

class ProductionDAGEventLoop(DAGEventLoop):
    """Production-grade DAG with validation."""
    
    def __init__(self, ...):
        super().__init__(...)
        
        # Setup validator
        config = ValidationConfig(
            min_quality_score=75.0,
            outlier_method=OutlierMethod.Z_SCORE,
            gap_strategy=GapHandlingStrategy.LINEAR_INTERPOLATE,
            enable_fallback=True
        )
        
        self.validator = MarketDataValidator(config)
        self.validated_feed = ValidatedDataFeed(
            self.validator,
            min_quality_score=75.0
        )
        
        # Register fallbacks
        self.validator.register_fallback_source(
            "backup_exchange",
            self.fetch_from_backup
        )
    
    async def _process_event(self, event):
        # Get validated data
        df = await self.validated_feed.get_data(
            symbol=event.symbol,
            timeframe=self.timeframe,
            raw_fetcher=lambda: self.fetch_raw_data(event.symbol)
        )
        
        if df is None:
            # Get report for logging
            report = self.validated_feed.get_quality_report(
                event.symbol, self.timeframe
            )
            
            logger.warning(
                f"Skipping {event.symbol}: quality={report.quality_score:.1f} "
                f"issues={len(report.issues)}"
            )
            
            # Log to monitoring
            self.metrics.record("dag_skipped_due_to_quality", 1, {
                "symbol": event.symbol,
                "quality_score": report.quality_score
            })
            
            return
        
        # Data is clean - execute DAG
        result = self.execute_dag(df)
        
        # Log success
        report = self.validated_feed.get_quality_report(
            event.symbol, self.timeframe
        )
        
        self.metrics.record("dag_executed", 1, {
            "symbol": event.symbol,
            "quality_score": report.quality_score
        })
```

---

## Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `backend/market_data_validation.py` | ~600 | **NEW** - Data validation system |
| `main.py` | +3 | Added validation router import & registration |

---

## Status: ✅ COMPLETE

Market data validation layer with:
- ✅ 4 outlier detection methods
- ✅ 4 gap handling strategies
- ✅ OHLC integrity checks
- ✅ Quality scoring (0-100)
- ✅ Fallback mechanisms
- ✅ DAG pre-execution validation
