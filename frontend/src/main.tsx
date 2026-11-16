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
    <h1>מספרה</h1>
    {error && <p role="alert">{error}</p>}
    {session === undefined ? <p role="status">בודקים את מצב הכניסה…</p>
      : session ? <button disabled={busy} onClick={logout}>יציאה</button>
      : <form onSubmit={login}>
        <h2>כניסה לסביבת פיתוח</h2>
        <label htmlFor="access-code">קוד גישה</label>
        <input id="access-code" name="access_code" type="password" dir="ltr" autoComplete="off" required maxLength={60}/>
        <button disabled={busy}>כניסה</button>
      </form>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<App />);
