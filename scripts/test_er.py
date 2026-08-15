import sys
sys.path.insert(0, '.')

print("1. importing hashlib, logging, datetime, enum, typing, uuid...")
import hashlib, logging, datetime, enum, typing, uuid
print("2. importing pydantic...")
from pydantic import BaseModel, ConfigDict, Field
print("3. importing sqlalchemy...")
from sqlalchemy import JSON, Column, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Index, String, bindparam, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session
print("4. importing Base from backend_app.core.database...")
from backend_app.core.database import Base
print("5. importing metrics from backend_app.core.metrics...")
from backend_app.core.metrics import execution_metrics
print("6. all dependencies imported! Now importing execution_record module...")
try:
    import backend_app.core.models.execution_record as er
    print("7. execution_record imported successfully!")
except Exception as e:
    import traceback
    traceback.print_exc()
