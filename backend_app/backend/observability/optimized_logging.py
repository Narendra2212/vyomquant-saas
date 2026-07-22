"""
HFT-Optimized Async Logging System

Ultra-low latency logging optimized for high-frequency trading.
Addresses sync I/O blocking, event loop blocking, and performance issues.

Author: Senior Institutional Systems Architect
"""

import asyncio
import json
import logging
import logging.handlers
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("optimized_logging")


class AsyncLogHandler:
    """Async log handler that doesn't block the event loop."""
    
    def __init__(self, 
                 max_queue_size: int = 10000,
                 batch_size: int = 100,
                 flush_interval: float = 1.0,
                 worker_threads: int = 2):
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.worker_threads = worker_threads
        
        # Async-safe queue for log entries
        self._log_queue = asyncio.Queue(maxsize=max_queue_size)
        self._queue_lock = threading.Lock()
        
        # Background processing
        self._running = False
        self._processor_task = None
        self._flush_task = None
        
        # Thread pool for I/O operations
        self._executor = ThreadPoolExecutor(
            max_workers=worker_threads,
            thread_name_prefix="async_log"
        )
        
        # Performance metrics
        self._dropped_logs = 0
        self._processed_logs = 0
        self._last_stats_time = time.time()
        
        # Destination handlers
        self._handlers: List[logging.Handler] = []
    
    def add_handler(self, handler: logging.Handler):
        """Add a destination handler."""
        self._handlers.append(handler)
    
    def emit(self, record: logging.LogRecord):
        """Emit log record asynchronously."""
        try:
            # Try to add to queue
            self._log_queue.put_nowait(record)
        except asyncio.QueueFull:
            # Drop log if queue full (HFT optimization)
            with self._queue_lock:
                self._dropped_logs += 1
    
    async def start(self):
        """Start async log processing."""
        if self._running:
            return
        
        self._running = True
        self._processor_task = asyncio.create_task(self._process_logs())
        self._flush_task = asyncio.create_task(self._periodic_flush())
        
        logger.info("Async log handler started")
    
    async def stop(self):
        """Stop async log processing."""
        self._running = False
        
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        
        # Process remaining logs
        await self._flush_remaining_logs()
        
        # Shutdown thread pool
        self._executor.shutdown(wait=True)
        
        logger.info("Async log handler stopped")
    
    async def _process_logs(self):
        """Background task to process log entries."""
        while self._running:
            try:
                batch = []
                deadline = time.time() + self.flush_interval
                
                # Collect batch
                while len(batch) < self.batch_size and time.time() < deadline:
                    try:
                        timeout = max(0.1, deadline - time.time())
                        record = await asyncio.wait_for(
                            self._log_queue.get(), 
                            timeout=timeout
                        )
                        batch.append(record)
                    except asyncio.TimeoutError:
                        break
                
                # Process batch if we have logs
                if batch:
                    await self._process_batch(batch)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Log processor error: {e}")
                await asyncio.sleep(0.1)
    
    async def _process_batch(self, batch: List[logging.LogRecord]):
        """Process a batch of log records."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._process_batch_sync, batch)
    
    def _process_batch_sync(self, batch: List[logging.LogRecord]):
        """Synchronous batch processing (runs in thread pool)."""
        for record in batch:
            for handler in self._handlers:
                try:
                    handler.emit(record)
                except Exception:
                    pass  # Don't let logging errors break the app
        
        with self._queue_lock:
            self._processed_logs += len(batch)
    
    async def _periodic_flush(self):
        """Periodic flush task."""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush_remaining_logs()
            except asyncio.CancelledError:
                break
    
    async def _flush_remaining_logs(self):
        """Flush all remaining logs in queue."""
        remaining = []
        while not self._log_queue.empty():
            try:
                record = self._log_queue.get_nowait()
                remaining.append(record)
            except asyncio.QueueEmpty:
                break
        
        if remaining:
            await self._process_batch(remaining)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get logging statistics."""
        with self._queue_lock:
            return {
                'queue_size': self._log_queue.qsize(),
                'max_queue_size': self.max_queue_size,
                'dropped_logs': self._dropped_logs,
                'processed_logs': self._processed_logs,
                'drop_rate': self._dropped_logs / max(self._processed_logs, 1) * 100
            }


class OptimizedJSONFormatter:
    """Optimized JSON formatter with minimal overhead."""
    
    def __init__(self, 
                 include_extra: bool = True,
                 compact: bool = True,
                 skip_fields: List[str] = None):
        self.include_extra = include_extra
        self.compact = compact
        self.skip_fields = skip_fields or []
        
        # Pre-compiled field names for performance
        self._timestamp = "timestamp"
        self._level = "level"
        self._logger = "logger"
        self._message = "message"
        self._source = "source"
        self._thread = "thread"
        self._process = "process"
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with minimal overhead."""
        # Build log data dict efficiently
        log_data = {
            self._timestamp: datetime.utcnow().isoformat() + "Z",
            self._level: record.levelname,
            self._logger: record.name,
            self._message: record.getMessage(),
            self._source: {
                "file": record.filename,
                "line": record.lineno,
                "function": record.funcName,
            },
            self._thread: record.thread,
            self._process: record.process,
        }
        
        # Add extra fields if enabled
        if self.include_extra and hasattr(record, '__dict__'):
            for key, value in record.__dict__.items():
                if (key not in self.skip_fields and 
                    key not in ['name', 'msg', 'args', 'levelname', 'levelno', 
                               'pathname', 'filename', 'module', 'lineno', 
                               'funcName', 'created', 'msecs', 'relativeCreated',
                               'thread', 'threadName', 'processName', 'process']):
                    log_data[key] = value
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Fast JSON serialization
        separators = (',', ':') if self.compact else None
        return json.dumps(log_data, separators=separators, default=str)


class OptimizedContextualLogger:
    """Optimized contextual logger with minimal overhead."""
    
    def __init__(self, logger: logging.Logger):
        self._logger = logger
        # Use asyncio.Task.current_task() for async context
        self._context_key = "_optimized_log_context"
    
    def set_context(self, **kwargs):
        """Set context fields for all subsequent logs."""
        try:
            task = asyncio.current_task()
            if task:
                if not hasattr(task, self._context_key):
                    setattr(task, self._context_key, {})
                context = getattr(task, self._context_key)
                context.update(kwargs)
        except RuntimeError:
            # No event loop - use thread local
            pass
    
    def clear_context(self):
        """Clear all context fields."""
        try:
            task = asyncio.current_task()
            if task and hasattr(task, self._context_key):
                delattr(task, self._context_key)
        except RuntimeError:
            pass
    
    def _get_context(self) -> Dict[str, Any]:
        """Get current context."""
        try:
            task = asyncio.current_task()
            if task and hasattr(task, self._context_key):
                return getattr(task, self._context_key)
        except RuntimeError:
            pass
        return {}
    
    def _get_extra(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build extra dict with context fields."""
        result = {}
        
        # Add context fields
        context = self._get_context()
        result.update(context)
        
        # Add explicit extra fields
        if extra:
            result.update(extra)
        
        return result
    
    def debug(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self._logger.debug(msg, extra={"extra": self._get_extra(extra)})
    
    def info(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self._logger.info(msg, extra={"extra": self._get_extra(extra)})
    
    def warning(self, msg: str, extra: Optional[Dict[str, Any]] = None):
        self._logger.warning(msg, extra={"extra": self._get_extra(extra)})
    
    def error(self, msg: str, extra: Optional[Dict[str, Any]] = None, exc_info=None):
        self._logger.error(msg, extra={"extra": self._get_extra(extra)}, exc_info=exc_info)
    
    def critical(self, msg: str, extra: Optional[Dict[str, Any]] = None, exc_info=None):
        self._logger.critical(msg, extra={"extra": self._get_extra(extra)}, exc_info=exc_info)


class OptimizedLogManager:
    """Optimized log manager with HFT performance."""
    
    def __init__(self):
        self.async_handler = None
        self.formatter = None
        self.initialized = False
        
        # Performance optimization
        self._buffer_size = 8192  # 8KB buffer for file I/O
        self._rotation_max_bytes = 100 * 1024 * 1024  # 100MB
        self._rotation_backup_count = 10
    
    async def initialize(self,
                      level: str = "INFO",
                      log_file: Optional[str] = None,
                      json_format: bool = True,
                      loki_url: Optional[str] = None):
        """Initialize optimized logging system."""
        if self.initialized:
            return
        
        # Create optimized formatter
        self.formatter = OptimizedJSONFormatter(
            include_extra=True,
            compact=True,
            skip_fields=['args', 'created', 'msecs', 'relativeCreated']
        )
        
        # Create async handler
        self.async_handler = AsyncLogHandler(
            max_queue_size=10000,
            batch_size=100,
            flush_interval=1.0,
            worker_threads=2
        )
        
        # Add file handler if specified
        if log_file:
            file_handler = logging.handlers.RotatingFileHandler(
                filename=log_file,
                maxBytes=self._rotation_max_bytes,
                backupCount=self._rotation_backup_count,
                encoding='utf-8'
            )
            file_handler.setFormatter(self.formatter)
            self.async_handler.add_handler(file_handler)
        
        # Add console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self.formatter)
        self.async_handler.add_handler(console_handler)
        
        # Configure root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, level.upper()))
        
        # Remove existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Add async handler
        root_logger.addHandler(self.async_handler)
        
        # Start async processing
        await self.async_handler.start()
        
        self.initialized = True
        logger.info("Optimized logging system initialized")
    
    async def shutdown(self):
        """Shutdown optimized logging system."""
        if self.async_handler:
            await self.async_handler.stop()
        
        logger.info("Optimized logging system shutdown")
    
    def get_logger(self, name: str) -> OptimizedContextualLogger:
        """Get optimized contextual logger."""
        if not self.initialized:
            # Fallback to standard logger
            return OptimizedContextualLogger(logging.getLogger(name))
        
        return OptimizedContextualLogger(logging.getLogger(name))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get logging statistics."""
        if self.async_handler:
            return self.async_handler.get_stats()
        return {}


# Global optimized log manager
optimized_log_manager = OptimizedLogManager()


async def setup_optimized_logging(level: str = "INFO",
                               log_file: Optional[str] = None,
                               json_format: bool = True,
                               loki_url: Optional[str] = None):
    """Setup optimized logging system."""
    await optimized_log_manager.initialize(
        level=level,
        log_file=log_file,
        json_format=json_format,
        loki_url=loki_url
    )


def get_optimized_logger(name: str) -> OptimizedContextualLogger:
    """Get optimized contextual logger instance."""
    return optimized_log_manager.get_logger(name)


async def shutdown_optimized_logging():
    """Shutdown optimized logging system."""
    await optimized_log_manager.shutdown()


# Optimized structured log helpers
@dataclass
class OptimizedTradeLogEntry:
    """Optimized trade log entry."""
    trade_id: str
    timestamp: str
    symbol: str
    side: str
    quantity: float
    price: float
    pnl: Optional[float] = None
    strategy: Optional[str] = None
    exchange: Optional[str] = None
    order_id: Optional[str] = None
    execution_time_us: Optional[float] = None  # Microseconds for HFT
    tenant_id: Optional[str] = None


@dataclass
class OptimizedErrorLogEntry:
    """Optimized error log entry."""
    error_id: str
    timestamp: str
    level: str
    component: str
    message: str
    exception: Optional[str] = None
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


# Dedicated optimized loggers
trade_logger = get_optimized_logger("trades")
error_logger = get_optimized_logger("errors")
system_logger = get_optimized_logger("system")
audit_logger = get_optimized_logger("audit")


async def log_trade_optimized(trade_data: Dict[str, Any]):
    """Optimized trade logging with minimal overhead."""
    entry = OptimizedTradeLogEntry(
        trade_id=trade_data.get("trade_id", ""),
        timestamp=datetime.utcnow().isoformat() + "Z",
        symbol=trade_data.get("symbol", ""),
        side=trade_data.get("side", ""),
        quantity=trade_data.get("quantity", 0.0),
        price=trade_data.get("price", 0.0),
        pnl=trade_data.get("pnl"),
        strategy=trade_data.get("strategy"),
        exchange=trade_data.get("exchange"),
        order_id=trade_data.get("order_id"),
        execution_time_us=trade_data.get("execution_time_us"),  # HFT precision
        tenant_id=trade_data.get("tenant_id"),
    )
    
    trade_logger.info(
        "Trade executed",
        extra={"trade": asdict(entry), "log_type": "trade"}
    )


async def log_error_optimized(error_data: Dict[str, Any], exception: Optional[Exception] = None):
    """Optimized error logging with minimal overhead."""
    exc_str = None
    if exception:
        import traceback
        exc_str = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        ))
    
    entry = OptimizedErrorLogEntry(
        error_id=error_data.get("error_id", ""),
        timestamp=datetime.utcnow().isoformat() + "Z",
        level=error_data.get("level", "ERROR"),
        component=error_data.get("component", ""),
        message=error_data.get("message", ""),
        exception=exc_str,
        trace_id=error_data.get("trace_id"),
        tenant_id=error_data.get("tenant_id"),
        context=error_data.get("context"),
    )
    
    level = getattr(logging, entry.level.upper(), logging.ERROR)
    if level >= logging.ERROR:
        error_logger.error(
            entry.message,
            extra={"error": asdict(entry), "log_type": "error"},
            exc_info=exception is not None
        )
    else:
        error_logger.warning(
            entry.message,
            extra={"error": asdict(entry), "log_type": "error"}
        )
