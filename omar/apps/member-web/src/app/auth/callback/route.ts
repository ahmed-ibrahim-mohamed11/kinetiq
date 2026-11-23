import { NextResponse, type NextRequest } from 'next/server';
import { createClient } from '@smartgym/supabase-client/server';

/** Magic-link / OTP landing: exchange the code for a session cookie, then continue. */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  const next = searchParams.get('next');
  const safeNext = next && next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard';

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${origin}${safeNext}`);
  }
  const login = new URL('/login', origin);
  login.searchParams.set('error', 'link_invalid');
  return NextResponse.redirect(login);
}
