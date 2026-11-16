import {useEffect, useState} from 'react';
import type {FormEvent} from 'react';
import {createRoot} from 'react-dom/client';
import {api, ApiError} from './api';
import type {Session} from './api';
import './style.css';

type Identity = Session | null | undefined;

function App() {
  const [session, setSession] = useState<Identity>(undefined);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const slug = window.location.pathname.split('/')[2];
  const [business, setBusiness] = useState<string | null | undefined>();
  const [tenant, setTenant] = useState<{session: Session; matched: boolean}>();

  useEffect(() => {
    const controller = new AbortController();
    api<{display_name: string}>('/public/businesses/' + encodeURIComponent(slug), {signal: controller.signal})
      .then(value => setBusiness(value.display_name), () => { if (!controller.signal.aborted) setBusiness(null); });
    return () => controller.abort();
  }, [slug]);

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    api('/auth/tenant-context/' + encodeURIComponent(slug), {signal: controller.signal}).then(
      () => setTenant({session, matched: true}),
      () => { if (!controller.signal.aborted) setTenant({session, matched: false}); },
    );
    return () => controller.abort();
  }, [slug, session]);

  useEffect(() => {
    let active = true;
    const restore = () => {
      setSession(undefined);
      api<Session>('/auth/me').then(
        value => { if (active) setSession(value); },
        failure => {
          if (!active) return;
          if (!(failure instanceof ApiError && failure.status === 401)) setError('לא ניתן לבדוק את מצב הכניסה.');
          setSession(null);
        },
      );
    };
    restore();
    window.addEventListener('pageshow', restore);
    return () => {
      active = false;
      window.removeEventListener('pageshow', restore);
    };
  }, []);

  useEffect(() => {
    if (!session) return;
    const timer = window.setTimeout(() => setSession(null), Math.max(0, Date.parse(session.expires_at) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [session]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const accessCode = new FormData(form).get('access_code');
    form.reset();
    setBusy(true);
    setError('');
    try {
      setSession(await api<Session>('/auth/dev-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Protection': '1'},
        body: JSON.stringify({access_code: accessCode}),
      }));
    } catch (failure) {
      setError(failure instanceof ApiError && failure.status === 429
        ? 'יותר מדי ניסיונות. אפשר לנסות שוב בהמשך.' : 'הכניסה נכשלה.');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (!session) return;
    setBusy(true);
    setError('');
    try {
      await api('/auth/session', {method: 'DELETE', headers: {'X-CSRF-Token': session.csrf_token}});
      setSession(null);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) setSession(null);
      else setError('היציאה נכשלה. ניתן לנסות שוב.');
    } finally {
      setBusy(false);
    }
  }

  return <main>
    <h1><bdi>{business ?? "מספרה"}</bdi></h1>
    {business === null && <p role="alert">המספרה אינה זמינה.</p>}
    {session && tenant?.session === session && !tenant.matched && <p role="alert">החיבור אינו מתאים למספרה זו.</p>}
    {error && <p role="alert">{error}</p>}
    {session === undefined ? <p role="status">בודקים את מצב הכניסה…</p>
      : session ? <button disabled={busy} onClick={logout}>יציאה</button>
      : business && <form onSubmit={login}>
        <h2>כניסה לסביבת פיתוח</h2>
        <label htmlFor="access-code">קוד גישה</label>
        <input id="access-code" name="access_code" type="password" dir="ltr" autoComplete="off" required maxLength={60}/>
        <button disabled={busy}>כניסה</button>
      </form>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<App />);
