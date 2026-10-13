"""DB-01: backend_app 021+ migrations must be applied after migrations/ (024 needs its tables)."""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("provision_database", ROOT / "scripts" / "provision_database.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_late_backend_migrations_follow_the_migrations_directory():
    files = _load()._numbered_sql_files()
    groups = []
    for f in files:
        if f.parent == ROOT / "migrations":
            g = "migrations"
        else:
            g = "early" if int(f.name[:3]) < 20 else "late"
        if not groups or groups[-1] != g:
            groups.append(g)
    assert groups == ["early", "migrations", "late"], groups


def test_024_comes_after_the_file_that_declares_referral_commissions():
    files = _load()._numbered_sql_files()
    order = [str(f.relative_to(ROOT)) for f in files]
    declaring = [i for i, f in enumerate(files)
                 if f.parent == ROOT / "migrations"
                 and "create table" in f.read_text(errors="replace").lower()
                 and "referral_commissions" in f.read_text(errors="replace")]
    assert declaring, "no migrations/ file declares referral_commissions"
    i024 = next(i for i, p in enumerate(order) if p.endswith("backend_app/migrations/024_" + p.split("024_")[-1]) and "/024_" in p)
    assert i024 > min(declaring)


def test_step0_file_is_not_in_the_numbered_set():
    files = _load()._numbered_sql_files()
    assert not any(f.name.startswith("020") for f in files)
