/**
 * Supabase client configuration — lib/supabase.js
 * Shared by landing, waitlist, admin, and app components.
 * Re-exports the existing validated client from src/supabase.js
 * and adds a service-role admin client for admin operations.
 */

import { supabase as _supabase } from '../supabase'
import { createClient } from '@supabase/supabase-js'

// Re-export the production-validated client as named export
export const supabase = _supabase
export default supabase

// Admin client (service role) for admin dashboard operations
// Falls back to anon key if service key not provided (read-limited)
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY
const supabaseServiceKey = import.meta.env.VITE_SUPABASE_SERVICE_KEY

export const supabaseAdmin = supabaseUrl && supabaseAnonKey
  ? createClient(
      supabaseUrl,
      supabaseServiceKey || supabaseAnonKey,
      {
        auth: {
          autoRefreshToken: false,
          persistSession: false,
        },
      }
    )
  : null
