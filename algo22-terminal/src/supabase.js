/**
 * Supabase client configuration
 * Handles authentication and database operations
 */

import { createClient } from '@supabase/supabase-js';

// Environment variables
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

// Production-grade env validation
const validateSupabaseConfig = () => {
  const errors = [];
  
  if (!supabaseUrl) {
    errors.push('VITE_SUPABASE_URL is missing');
  } else {
    // Validate URL format
    try {
      const url = new URL(supabaseUrl);
      if (!url.hostname.endsWith('.supabase.co')) {
        errors.push(`VITE_SUPABASE_URL has invalid hostname: ${url.hostname}. Expected *.supabase.co`);
      }
      if (url.protocol !== 'https:') {
        errors.push(`VITE_SUPABASE_URL must use HTTPS protocol`);
      }
    } catch (e) {
      errors.push(`VITE_SUPABASE_URL is malformed: ${supabaseUrl}`);
    }
  }
  
  if (!supabaseAnonKey) {
    errors.push('VITE_SUPABASE_ANON_KEY is missing');
  } else if (supabaseAnonKey.length < 50) {
    errors.push('VITE_SUPABASE_ANON_KEY appears invalid (too short)');
  }
  
  if (errors.length > 0) {
    console.error('🔴 SUPABASE CONFIGURATION ERRORS:', errors);
    throw new Error(`Supabase configuration invalid: ${errors.join('; ')}`);
  }
  
  // Log successful validation
  console.log('🟢 Supabase configuration validated:', {
    url: supabaseUrl,
    hasAnonKey: !!supabaseAnonKey,
    anonKeyLength: supabaseAnonKey?.length
  });
};

// Validate configuration before creating client
let supabase;
try {
  validateSupabaseConfig();
  // Create and export Supabase client
  supabase = createClient(supabaseUrl, supabaseAnonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true
    }
  });
} catch (error) {
  console.error('Supabase configuration validation failed:', error.message);
  console.warn('App will run without Supabase functionality');
  // Create a dummy/mock client to prevent crashes
  supabase = {
    auth: {
      signInWithPassword: () => ({ data: null, error: new Error('Supabase not configured') }),
      signInWithOtp: () => ({ data: null, error: new Error('Supabase not configured') }),
      verifyOtp: () => ({ data: null, error: new Error('Supabase not configured') }),
      signInWithOAuth: () => ({ data: null, error: new Error('Supabase not configured') }),
      signUp: () => ({ data: null, error: new Error('Supabase not configured') }),
      signOut: () => ({ error: null }),
      getUser: () => ({ data: { user: null }, error: new Error('Supabase not configured') }),
      updateUser: () => ({ data: null, error: new Error('Supabase not configured') }),
    },
    from: () => ({
      select: () => ({ data: [], error: new Error('Supabase not configured') }),
      insert: () => ({ data: null, error: new Error('Supabase not configured') }),
      update: () => ({ data: null, error: new Error('Supabase not configured') }),
      delete: () => ({ data: null, error: new Error('Supabase not configured') }),
    }),
  };
}

export { supabase };
export default supabase;
