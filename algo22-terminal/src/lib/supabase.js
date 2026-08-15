/**
 * Supabase client configuration — lib/supabase.js
 * Single-instance singleton re-export from src/supabase.js.
 * Guarantees zero duplicate GoTrueClient instances in browser context.
 */

import { supabase as _supabase } from '../supabase'

// Re-export the single validated client singleton
export const supabase = _supabase
export const supabaseAdmin = _supabase
export default _supabase
