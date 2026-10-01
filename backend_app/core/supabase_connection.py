"""
Supabase Connection - Secure Supabase Client Access (Production + Fallback)

Provides a secure interface to Supabase with safe fallback mode.
 Falls back gracefully if Supabase is not configured.
"""

from backend_app.core.config import settings
from supabase import create_client


class SupabaseConnection:
    """
    Supabase Connection for secure Supabase client management.
    
    Features:
    - Safe initialization with fallback mode
    - Service Role authentication (when configured)
    - Graceful degradation if Supabase unavailable
    """
    
    def __init__(self):
        """
        Initialize SupabaseConnection with safe fallback.
        Logs status but does NOT crash if Supabase not configured.
        """
        self.client = None
        self.is_configured = False
        
        # Check if Supabase is configured
        if settings.SUPABASE_URL and settings.SUPABASE_SERVICE_ROLE_KEY:
            try:
                # Create authenticated client with service role
                self.client = create_client(
                    settings.SUPABASE_URL,
                    settings.SUPABASE_SERVICE_ROLE_KEY
                )
                self.is_configured = True
                print(" Supabase: CONNECTED")
            except Exception as e:
                print(f"  Supabase connection failed: {e}")
                print("  Supabase: FALLBACK MODE (dev tokens active)")
        else:
            missing = []
            if not settings.SUPABASE_URL:
                missing.append("SUPABASE_URL")
            if not settings.SUPABASE_SERVICE_ROLE_KEY:
                missing.append("SUPABASE_SERVICE_ROLE_KEY")
            print(f"  Supabase: NOT CONFIGURED (missing: {', '.join(missing)})")
            print("  Supabase: FALLBACK MODE (dev tokens active)")
    
    def get_client(self):
        """
        Get the Supabase client if configured.
        
        Returns:
            Supabase client instance or None if not configured
            
        Example:
            vault = SupabaseConnection()
            client = vault.get_client()
            if client:
                result = client.table("strategy_versions").select("*").execute()

        Note: there is no ``public.users`` table in this schema - Supabase keeps
        users in ``auth.users``, which PostgREST does not expose. The example
        above deliberately names a table this application actually owns and
        declares (``backend_app/migrations/001_strategy_architecture.sql``).
        """
        return self.client
    
    def is_available(self) -> bool:
        """Check if Supabase is properly configured and available"""
        return self.is_configured and self.client is not None
    
    def health_check(self) -> bool:
        """
        Verify Supabase connection is healthy.
        
        Returns:
            True if connection is working, False otherwise
        
        The probe reads ``strategy_versions``. That choice is not arbitrary and
        ``tests/test_schema_table_reference_drift.py`` pins it: the relation
        exists in the production database, it is declared by a migration in
        this repository (``001_strategy_architecture.sql``), and it is owned by
        this application rather than by the Supabase platform.
        
        It used to read ``users``, and that is why this method was broken.
        There is no ``public.users`` in this schema - Supabase keeps users in
        ``auth.users``, which PostgREST does not expose - so every call got
        ``42P01``, the bare ``except`` below swallowed it, and ``health_check``
        returned ``False`` *whenever the client was configured*. It could only
        ever return ``False``, which is the one answer a health check must not
        give unconditionally. Nothing in the repository calls it (``routers/
        health.py`` included), which is why that went unnoticed.
        """
        if not self.client:
            return False
        
        try:
            # Cheapest query that still proves the round trip: one column, one
            # row, no count. The previous form asked for count="exact", which
            # makes PostgREST count the whole relation - needless work for a
            # liveness probe, and the cost grows with the table.
            self.client.table("strategy_versions").select("id").limit(1).execute()
            return True
        except Exception:
            return False


# Singleton instance for application-wide use
_vault_instance: SupabaseConnection | None = None


def get_supabase_connection() -> SupabaseConnection:
    """
    Get or create the singleton SupabaseConnection instance.
    
    Returns:
        SupabaseConnection singleton instance
        
    Example:
        from backend_app.core.supabase_connection import get_supabase_connection
        
        vault = get_supabase_connection()
        client = vault.get_client()
    """
    global _vault_instance
    
    if _vault_instance is None:
        _vault_instance = SupabaseConnection()
    
    return _vault_instance


def reset_connection() -> None:
    """
    Reset the vault singleton (useful for testing).
    """
    global _vault_instance
    _vault_instance = None
