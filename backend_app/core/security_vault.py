"""
Security Vault - Secure Supabase Client Access (Production + Fallback)

Provides a secure interface to Supabase with safe fallback mode.
 Falls back gracefully if Supabase is not configured.
"""

from supabase import create_client
from backend_app.core.config import settings


class SecurityVault:
    """
    Security Vault for secure Supabase client management.
    
    Features:
    - Safe initialization with fallback mode
    - Service Role authentication (when configured)
    - Graceful degradation if Supabase unavailable
    """
    
    def __init__(self):
        """
        Initialize SecurityVault with safe fallback.
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
            vault = SecurityVault()
            client = vault.get_client()
            if client:
                result = client.table("users").select("*").execute()
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
        """
        if not self.client:
            return False
        
        try:
            # Simple query to verify connection
            self.client.table("users").select("count", count="exact").limit(1).execute()
            return True
        except Exception:
            return False


# Singleton instance for application-wide use
_vault_instance: SecurityVault | None = None


def get_security_vault() -> SecurityVault:
    """
    Get or create the singleton SecurityVault instance.
    
    Returns:
        SecurityVault singleton instance
        
    Example:
        from backend_app.core.security_vault import get_security_vault
        
        vault = get_security_vault()
        client = vault.get_client()
    """
    global _vault_instance
    
    if _vault_instance is None:
        _vault_instance = SecurityVault()
    
    return _vault_instance


def reset_vault() -> None:
    """
    Reset the vault singleton (useful for testing).
    """
    global _vault_instance
    _vault_instance = None
