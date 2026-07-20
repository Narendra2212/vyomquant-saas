# Compilation Verification Report

> [!WARNING]
> Found 13 modules failing to import during verification.

## Import Failures

### backend_app.alembic.env
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\alembic\env.py", line 18, in <module>
    config.set_main_option(
    ^^^^^^^^^^^^^^^^^^^^^^
AttributeError: 'NoneType' object has no attribute 'set_main_option'

```

### backend_app.backend.atomic_persistence_coordinator
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\atomic_persistence_coordinator.py", line 108, in <module>
    @dataclass
     ^^^^^^^^^
  File "C:\Python314\Lib\dataclasses.py", line 1442, in dataclass
    return wrap(cls)
  File "C:\Python314\Lib\dataclasses.py", line 1432, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "C:\Python314\Lib\dataclasses.py", line 1136, in _process_class
    _init_fn(all_init_fields,
    ~~~~~~~~^^^^^^^^^^^^^^^^^
             std_init_fields,
             ^^^^^^^^^^^^^^^^
    ...<9 lines>...
             slots,
             ^^^^^^
             )
             ^
  File "C:\Python314\Lib\dataclasses.py", line 687, in _init_fn
    raise TypeError(f'non-default argument {f.name!r} '
                    f'follows default argument {seen_default.name!r}')
TypeError: non-default argument 'created_at' follows default argument 'database_snapshot'

```

### backend_app.backend.distributed_execution.execution_coordinator
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\execution_coordinator.py", line 23, in <module>
    from .replay_engine import immutable_journal, sequence_manager
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\replay_engine.py", line 835, in <module>
    class ValidationResult:
    ...<7 lines>...
            self.is_valid = self.success
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\replay_engine.py", line 840, in ValidationResult
    is_valid: bool = field(init=False)
                     ^^^^^
NameError: name 'field' is not defined

```

### backend_app.backend.distributed_execution.failover_coordinator
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\failover_coordinator.py", line 22, in <module>
    from .lease_manager import LeaseManager, LeaseRequest, LeaseType
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\lease_manager.py", line 48, in <module>
    @dataclass
     ^^^^^^^^^
  File "C:\Python314\Lib\dataclasses.py", line 1442, in dataclass
    return wrap(cls)
  File "C:\Python314\Lib\dataclasses.py", line 1432, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "C:\Python314\Lib\dataclasses.py", line 1136, in _process_class
    _init_fn(all_init_fields,
    ~~~~~~~~^^^^^^^^^^^^^^^^^
             std_init_fields,
             ^^^^^^^^^^^^^^^^
    ...<9 lines>...
             slots,
             ^^^^^^
             )
             ^
  File "C:\Python314\Lib\dataclasses.py", line 687, in _init_fn
    raise TypeError(f'non-default argument {f.name!r} '
                    f'follows default argument {seen_default.name!r}')
TypeError: non-default argument 'resource_id' follows default argument 'lease_id'

```

### backend_app.backend.distributed_execution.heartbeat_manager
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\heartbeat_manager.py", line 21, in <module>
    from .orchestration_safety_guarantees import (
    ...<3 lines>...
    )
ImportError: cannot import name 'OperationIsolationGuarantee' from 'backend_app.backend.distributed_execution.orchestration_safety_guarantees' (d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\orchestration_safety_guarantees.py)

```

### backend_app.backend.distributed_execution.leader_election_manager
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\leader_election_manager.py", line 24, in <module>
    from .lease_manager import LeaseManager, LeaseRequest, LeaseType
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\lease_manager.py", line 48, in <module>
    @dataclass
     ^^^^^^^^^
  File "C:\Python314\Lib\dataclasses.py", line 1442, in dataclass
    return wrap(cls)
  File "C:\Python314\Lib\dataclasses.py", line 1432, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "C:\Python314\Lib\dataclasses.py", line 1136, in _process_class
    _init_fn(all_init_fields,
    ~~~~~~~~^^^^^^^^^^^^^^^^^
             std_init_fields,
             ^^^^^^^^^^^^^^^^
    ...<9 lines>...
             slots,
             ^^^^^^
             )
             ^
  File "C:\Python314\Lib\dataclasses.py", line 687, in _init_fn
    raise TypeError(f'non-default argument {f.name!r} '
                    f'follows default argument {seen_default.name!r}')
TypeError: non-default argument 'resource_id' follows default argument 'lease_id'

```

### backend_app.backend.distributed_execution.lease_manager
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\lease_manager.py", line 48, in <module>
    @dataclass
     ^^^^^^^^^
  File "C:\Python314\Lib\dataclasses.py", line 1442, in dataclass
    return wrap(cls)
  File "C:\Python314\Lib\dataclasses.py", line 1432, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "C:\Python314\Lib\dataclasses.py", line 1136, in _process_class
    _init_fn(all_init_fields,
    ~~~~~~~~^^^^^^^^^^^^^^^^^
             std_init_fields,
             ^^^^^^^^^^^^^^^^
    ...<9 lines>...
             slots,
             ^^^^^^
             )
             ^
  File "C:\Python314\Lib\dataclasses.py", line 687, in _init_fn
    raise TypeError(f'non-default argument {f.name!r} '
                    f'follows default argument {seen_default.name!r}')
TypeError: non-default argument 'resource_id' follows default argument 'lease_id'

```

### backend_app.backend.distributed_execution.orphan_recovery_manager
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\orphan_recovery_manager.py", line 21, in <module>
    from .worker_registry import WorkerRegistry, WorkerInfo
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\worker_registry.py", line 21, in <module>
    from .deterministic_reassignment_model import DeterministicCapabilityAssigner
ModuleNotFoundError: No module named 'backend_app.backend.distributed_execution.deterministic_reassignment_model'

```

### backend_app.backend.distributed_execution.replay_engine
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\replay_engine.py", line 835, in <module>
    class ValidationResult:
    ...<7 lines>...
            self.is_valid = self.success
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\replay_engine.py", line 840, in ValidationResult
    is_valid: bool = field(init=False)
                     ^^^^^
NameError: name 'field' is not defined

```

### backend_app.backend.distributed_execution.standby_coordinator
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\standby_coordinator.py", line 22, in <module>
    from .lease_manager import LeaseManager, LeaseRequest, LeaseType
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\lease_manager.py", line 48, in <module>
    @dataclass
     ^^^^^^^^^
  File "C:\Python314\Lib\dataclasses.py", line 1442, in dataclass
    return wrap(cls)
  File "C:\Python314\Lib\dataclasses.py", line 1432, in wrap
    return _process_class(cls, init, repr, eq, order, unsafe_hash,
                          frozen, match_args, kw_only, slots,
                          weakref_slot)
  File "C:\Python314\Lib\dataclasses.py", line 1136, in _process_class
    _init_fn(all_init_fields,
    ~~~~~~~~^^^^^^^^^^^^^^^^^
             std_init_fields,
             ^^^^^^^^^^^^^^^^
    ...<9 lines>...
             slots,
             ^^^^^^
             )
             ^
  File "C:\Python314\Lib\dataclasses.py", line 687, in _init_fn
    raise TypeError(f'non-default argument {f.name!r} '
                    f'follows default argument {seen_default.name!r}')
TypeError: non-default argument 'resource_id' follows default argument 'lease_id'

```

### backend_app.backend.distributed_execution.worker_registry
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\backend\distributed_execution\worker_registry.py", line 21, in <module>
    from .deterministic_reassignment_model import DeterministicCapabilityAssigner
ModuleNotFoundError: No module named 'backend_app.backend.distributed_execution.deterministic_reassignment_model'

```

### backend_app.core.auth_middleware
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\core\auth_middleware.py", line 56, in <module>
    async def get_current_user(credentials=Depends(security)):
                                           ^^^^^^^
NameError: name 'Depends' is not defined

```

### backend_app.core.models.execution_tables
```python
Traceback (most recent call last):
  File "C:\Users\user\.gemini\antigravity-ide\brain\2065aa50-5d8d-479e-ab3b-3cafb12e4f08\scratch\test_imports.py", line 25, in <module>
    importlib.import_module(mod_name)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "C:\Python314\Lib\importlib\__init__.py", line 88, in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "<frozen importlib._bootstrap>", line 1398, in _gcd_import
  File "<frozen importlib._bootstrap>", line 1371, in _find_and_load
  File "<frozen importlib._bootstrap>", line 1342, in _find_and_load_unlocked
  File "<frozen importlib._bootstrap>", line 938, in _load_unlocked
  File "<frozen importlib._bootstrap_external>", line 759, in exec_module
  File "<frozen importlib._bootstrap>", line 491, in _call_with_frames_removed
  File "d:\aerora_quant_backend_updated_final1\backend_app\core\models\execution_tables.py", line 55, in <module>
    class Position(Base):
    ...<6 lines>...
        updated_at = Column(DateTime, default=datetime.utcnow)
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\orm\decl_api.py", line 199, in __init__
    _as_declarative(reg, cls, dict_)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\orm\decl_base.py", line 245, in _as_declarative
    return _MapperConfig.setup_mapping(registry, cls, dict_, None, {})
           ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\orm\decl_base.py", line 326, in setup_mapping
    return _ClassScanMapperConfig(
        registry, cls_, dict_, table, mapper_kw
    )
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\orm\decl_base.py", line 577, in __init__
    self._setup_table(table)
    ~~~~~~~~~~~~~~~~~^^^^^^^
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\orm\decl_base.py", line 1762, in _setup_table
    table_cls(
    ~~~~~~~~~^
        tablename,
        ^^^^^^^^^^
    ...<3 lines>...
        **table_kw,
        ^^^^^^^^^^^
    ),
    ^
  File "<string>", line 2, in __new__
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\util\deprecations.py", line 281, in warned
    return fn(*args, **kwargs)  # type: ignore[no-any-return]
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\sql\schema.py", line 429, in __new__
    return cls._new(*args, **kw)
           ~~~~~~~~^^^^^^^^^^^^^
  File "C:\Users\user\AppData\Roaming\Python\Python314\site-packages\sqlalchemy\sql\schema.py", line 461, in _new
    raise exc.InvalidRequestError(
    ...<5 lines>...
    )
sqlalchemy.exc.InvalidRequestError: Table 'positions' is already defined for this MetaData instance.  Specify 'extend_existing=True' to redefine options and columns on an existing Table object.

```

