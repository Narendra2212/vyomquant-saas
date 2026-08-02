"""
tests/test_schema_as_code_completeness.py — Schema-as-Code Completeness Test

Static test that validates all application tables have corresponding migrations.
This test parses migration SQL files and cross-checks table names against
application code usage to ensure schema-as-code completeness.

Author: Principal Software Architect
Date: 2025-08-02
"""

import sys
import os
import re
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class TestSchemaAsCodeCompleteness:
    """Test that all application tables have corresponding migrations."""

    def test_all_app_tables_have_migrations(self):
        """
        Test that every table referenced in application code has a CREATE TABLE
        statement in the migration files.
        """
        # Tables referenced in application code (from Phase 1 analysis)
        app_tables = {
            'strategies',
            'library_ratings',
            'library_strategies',
            'profiles',
            'library_subscriptions',
            'deployment_permissions',
            'strategy_research_reports',
            'strategy_deployments',
            'strategy_versions',
            'strategy_backtests',
            'exchange_connections',
            'notifications',
            'referral_profiles',
            'risk_settings',
            'execution_records',
            'signals',
            'exchange_keys',
            'users'
        }
        
        # Tables with CREATE TABLE in migrations (from Phase 1 analysis)
        migration_tables = {
            'strategy_versions',
            'strategy_deployments',
            'strategy_backtests',
            'marketplace_listings',
            'strategy_subscriptions',
            'strategy_research_reports',
            'signals',
            'signal_events',
            'risk_settings',
            'strategy_limits',
            'risk_settings_audit',
            'plans',
            'plan_features',
            'plan_limits',
            'plan_prices',
            'plan_migration_mapping',
            'referral_codes',
            'referral_relationships',
            'referral_commissions',
            'referral_wallets',
            'referral_payouts',
            'support_tickets',
            'ticket_comments',
            'notifications',
            'library_strategies',
            'library_ratings',
            'library_subscriptions',
            'deployment_permissions',
            'dag_tasks',
            'execution_records',
            'copilot_sessions',
            'copilot_messages',
            'waitlist',
            'creator_analytics',
            'marketplace_analytics',
            'exchange_keys',  # Now has migration
            'exchange_connections',  # Now has migration
            'referral_profiles'  # Now has migration
        }
        
        # Tables that are Supabase system tables (expected to not have migrations)
        supabase_system_tables = {
            'strategies',  # Part of Supabase's strategy system
            'profiles',  # Supabase auth system
            'users'  # Supabase auth system
        }
        
        # Find tables missing from migrations (excluding Supabase system tables)
        missing_tables = app_tables - migration_tables - supabase_system_tables
        
        if missing_tables:
            pytest.fail(
                f"The following application tables are missing from version control: {missing_tables}. "
                f"These tables are referenced in application code but have no CREATE TABLE statement in migration files. "
                f"Add proper migrations for these tables."
            )
        
        # Verify our new migrations are present
        assert 'exchange_keys' in migration_tables, "exchange_keys migration missing"
        assert 'exchange_connections' in migration_tables, "exchange_connections migration missing"
        assert 'referral_profiles' in migration_tables, "referral_profiles migration missing"

    def test_migration_files_exist(self):
        """Test that the new migration files we created actually exist."""
        migrations_dir = Path(__file__).parent.parent / "migrations"
        
        required_migrations = [
            "003_create_exchange_keys_table.sql",
            "004_create_exchange_connections_table.sql",
            "005_create_referral_profiles_table.sql"
        ]
        
        for migration_file in required_migrations:
            migration_path = migrations_dir / migration_file
            assert migration_path.exists(), f"Migration file {migration_file} does not exist"
            
            # Verify file is not empty
            content = migration_path.read_text(encoding='utf-8')
            assert len(content) > 100, f"Migration file {migration_file} appears to be empty or too short"
            
            # Verify it contains CREATE TABLE
            assert "CREATE TABLE" in content, f"Migration file {migration_file} missing CREATE TABLE statement"

    def test_migration_syntax_validity(self):
        """Test that migration files have valid SQL syntax."""
        migrations_dir = Path(__file__).parent.parent / "migrations"
        
        new_migrations = [
            "003_create_exchange_keys_table.sql",
            "004_create_exchange_connections_table.sql",
            "005_create_referral_profiles_table.sql"
        ]
        
        for migration_file in new_migrations:
            migration_path = migrations_dir / migration_file
            content = migration_path.read_text(encoding='utf-8')
            
            # Basic syntax validation
            assert "CREATE TABLE IF NOT EXISTS" in content, f"Missing CREATE TABLE IF NOT EXISTS in {migration_file}"
            assert "PRIMARY KEY" in content, f"Missing PRIMARY KEY in {migration_file}"
            assert "CREATE INDEX" in content, f"Missing indexes in {migration_file}"
            assert "ENABLE ROW LEVEL SECURITY" in content, f"Missing RLS in {migration_file}"
            assert "CREATE POLICY" in content, f"Missing RLS policy in {migration_file}"
            assert "BEGIN;" in content, f"Missing BEGIN statement in {migration_file}"
            assert "COMMIT;" in content, f"Missing COMMIT statement in {migration_file}"
            
            # Verify common patterns
            assert "user_id" in content, f"Missing user_id column in {migration_file}"
            assert "created_at" in content, f"Missing created_at column in {migration_file}"
            assert "updated_at" in content, f"Missing updated_at column in {migration_file}"

    def test_column_completeness_exchange_keys(self):
        """Test that exchange_keys migration has all columns used in code."""
        migration_path = Path(__file__).parent.parent / "migrations" / "003_create_exchange_keys_table.sql"
        content = migration_path.read_text(encoding='utf-8')
        
        # Columns used in application code
        required_columns = {
            'user_id',
            'exchange_id',
            'encrypted_api_key',
            'encrypted_secret_key',
            'encrypted_password'
        }
        
        for column in required_columns:
            assert column in content, f"Column {column} missing from exchange_keys migration"

    def test_column_completeness_exchange_connections(self):
        """Test that exchange_connections migration has all columns used in code."""
        migration_path = Path(__file__).parent.parent / "migrations" / "004_create_exchange_connections_table.sql"
        content = migration_path.read_text(encoding='utf-8')
        
        # Columns used in application code
        required_columns = {
            'user_id',
            'exchange_id',
            'is_active',
            'connection_status',
            'last_connected_at',
            'last_heartbeat_at',
            'error_message'
        }
        
        for column in required_columns:
            assert column in content, f"Column {column} missing from exchange_connections migration"

    def test_column_completeness_referral_profiles(self):
        """Test that referral_profiles migration has all columns used in code."""
        migration_path = Path(__file__).parent.parent / "migrations" / "005_create_referral_profiles_table.sql"
        content = migration_path.read_text(encoding='utf-8')
        
        # Columns used in application code
        required_columns = {
            'user_id',
            'referral_code',
            'total_referrals',
            'active_referrals',
            'pending_earnings',
            'approved_earnings',
            'paid_earnings',
            'lifetime_earnings'
        }
        
        for column in required_columns:
            assert column in content, f"Column {column} missing from referral_profiles migration"


def run_all_tests():
    """Run all schema-as-code completeness tests."""
    print("=" * 60)
    print("SCHEMA-AS-CODE COMPLETENESS TESTS")
    print("=" * 60)
    
    import pytest
    result = pytest.main([__file__, "-v", "--tb=short"])
    
    print("\n" + "=" * 60)
    if result == 0:
        print("ALL TESTS PASSED")
    else:
        print(f"TESTS FAILED (exit code: {result})")
    print("=" * 60)
    
    return result


if __name__ == "__main__":
    sys.exit(run_all_tests())
