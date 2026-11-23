/**
 * Notification worker core. The implementation lives in supabase/functions/_shared/notify
 * so the Supabase Edge Function (notification-dispatch) and this self-hosted worker share
 * one source of truth; this module re-exports it under the worker's historical paths.
 */
export * from '../../../supabase/functions/_shared/notify/worker.ts';
