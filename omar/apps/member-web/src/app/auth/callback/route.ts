import { NextResponse, type NextRequest } from 'next/server';
import { createClient } from '@smartgym/supabase-client/server';
import { safeNextPath } from '@smartgym/validation';

/** Magic-link / OTP landing: exchange the code for a session cookie, then continue. */
export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  const safeNext = safeNextPath(searchParams.get('next'));

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(`${origin}${safeNext}`);
  }
  const login = new URL('/login', origin);
  login.searchParams.set('error', 'link_invalid');
  return NextResponse.redirect(login);
}
