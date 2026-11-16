import {useCallback, useEffect, useRef, useState} from 'react';
import type {FormEvent} from 'react';
import {flushSync} from 'react-dom';
import {createRoot} from 'react-dom/client';
import {api, ApiError} from './api';
import type {Session} from './api';
import {Customer} from './customer';
import {resourceId} from './transport';
import './style.css';

type Identity = Session | null | undefined;
type Business = {status: 'ready'; name: string} | {status: 'missing' | 'error'};
type Tenant = {session: Session; status: 'ready' | 'mismatch' | 'error'};

function App() {
  const [session, setSession] = useState<Identity>(undefined);
  const [business, setBusiness] = useState<Business>();
  const [tenant, setTenant] = useState<Tenant>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [retryDelay, setRetryDelay] = useState(0);
  const [logoutPending, setLogoutPending] = useState(false);
  const [page, setPage] = useState(() => window.location.pathname.split('/').slice(3).join('/'));
  const slug = decodeURIComponent(window.location.pathname.split('/')[2]);
  const base = '/b/' + encodeURIComponent(slug);
  const restoreRequest = useRef<AbortController | null>(null);
  const actionPending = useRef(false);
  const sessionChannel = useRef<BroadcastChannel | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const loseSession = useCallback(() => {
    setLogoutPending(false);
    setTenant(undefined);
    setSession(null);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    api<{display_name: string}>('/public/businesses/' + encodeURIComponent(slug), {signal: controller.signal})
      .then(
        value => setBusiness({status: 'ready', name: value.display_name}),
        failure => {
          if (!controller.signal.aborted) setBusiness({
            status: failure instanceof ApiError && failure.status === 404 ? 'missing' : 'error',
          });
        },
      );
    return () => controller.abort();
  }, [slug]);

  useEffect(() => {
    if (!session || logoutPending) return;
    const controller = new AbortController();
    api('/auth/tenant-context/' + encodeURIComponent(slug), {signal: controller.signal}).then(
      () => { if (!controller.signal.aborted) setTenant({session, status: 'ready'}); },
      failure => {
        if (controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.status === 401) loseSession();
        else setTenant({session, status: failure instanceof ApiError && failure.status === 404 ? 'mismatch' : 'error'});
      },
    );
    return () => controller.abort();
  }, [slug, session, logoutPending, loseSession]);

  useEffect(() => {
    const restore = (immediate = false) => {
      if (actionPending.current) return;
      restoreRequest.current?.abort();
      const controller = new AbortController();
      restoreRequest.current = controller;
      const clearProtectedState = () => {
        setSession(undefined);
        setTenant(undefined);
      };
      if (immediate) flushSync(clearProtectedState);
      else clearProtectedState();
      api<Session>('/auth/me', {signal: controller.signal}).then(
        value => { if (!controller.signal.aborted) setSession(value); },
        failure => {
          if (controller.signal.aborted) return;
          if (!(failure instanceof ApiError && failure.status === 401)) setError('לא ניתן לבדוק את מצב הכניסה.');
          loseSession();
        },
      );
    };
    const revalidate = () => restore(true);
    const onPageShow = (event: PageTransitionEvent) => { if (event.persisted) revalidate(); };
    const onVisibility = () => { if (document.visibilityState === 'visible') revalidate(); };
    const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('barbershop-session') : null;
    sessionChannel.current = channel;
    if (channel) channel.onmessage = event => {
      if (event.data === 'changed') revalidate();
    };
    const onPageHide = (event: PageTransitionEvent) => {
      if (event.persisted) {
        restoreRequest.current?.abort();
        // Hide the cached document before the browser restores its visual state.
        document.documentElement.classList.add('restoring');
      }
    };
    const onPopState = () => {
      setPage(window.location.pathname.split('/').slice(3).join('/'));
      heading.current?.focus();
    };
    restore();
    window.addEventListener('pagehide', onPageHide);
    window.addEventListener('pageshow', onPageShow);
    window.addEventListener('popstate', onPopState);
    window.addEventListener('focus', revalidate);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      restoreRequest.current?.abort();
      window.removeEventListener('pagehide', onPageHide);
      window.removeEventListener('pageshow', onPageShow);
      window.removeEventListener('popstate', onPopState);
      window.removeEventListener('focus', revalidate);
      document.removeEventListener('visibilitychange', onVisibility);
      channel?.close();
      sessionChannel.current = null;
    };
  }, [loseSession]);

  useEffect(() => { document.documentElement.classList.remove('restoring'); }, [session]);

  useEffect(() => {
    if (!session) return;
    const expiry = Date.parse(session.expires_at);
    const check = () => { if (!Number.isFinite(expiry) || Date.now() >= expiry) loseSession(); };
    const timer = window.setTimeout(check, Math.max(0, expiry - Date.now()));
    document.addEventListener('visibilitychange', check);
    return () => {
      window.clearTimeout(timer);
      document.removeEventListener('visibilitychange', check);
    };
  }, [session, loseSession]);

  useEffect(() => {
    if (!retryDelay) return;
    const timer = window.setTimeout(() => setRetryDelay(0), retryDelay * 1000);
    return () => window.clearTimeout(timer);
  }, [retryDelay]);

  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (actionPending.current || retryDelay) return;
    const form = event.currentTarget;
    const accessCode = new FormData(form).get('access_code');
    form.reset();
    restoreRequest.current?.abort();
    actionPending.current = true;
    setBusy(true);
    setError('');
    try {
      setTenant(undefined);
      setLogoutPending(false);
      setSession(await api<Session>('/auth/dev-session', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Protection': '1'},
        body: JSON.stringify({access_code: accessCode}),
      }));
      sessionChannel.current?.postMessage('changed');
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 429) {
        setRetryDelay(failure.retryAfter);
        setError('יותר מדי ניסיונות. אפשר לנסות שוב בהמשך.');
      } else {
        setError('הכניסה נכשלה.');
      }
    } finally {
      actionPending.current = false;
      setBusy(false);
    }
  }

  async function logout() {
    if (!session || actionPending.current) return;
    restoreRequest.current?.abort();
    actionPending.current = true;
    setBusy(true);
    setLogoutPending(true);
    setTenant(undefined);
    setError('');
    try {
      await api('/auth/session', {method: 'DELETE', headers: {'X-CSRF-Token': session.csrf_token}});
      loseSession();
      sessionChannel.current?.postMessage('changed');
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 401) {
        loseSession();
        sessionChannel.current?.postMessage('changed');
      }
      else setError('היציאה נכשלה. ניתן לנסות שוב.');
    } finally {
      actionPending.current = false;
      setBusy(false);
    }
  }

  function navigate(target: string) {
    window.history.pushState(null, '', base + '/' + target);
    setPage(target);
    heading.current?.focus();
  }

  const owner = page.startsWith('owner/');
  const guarded = page !== '' && page !== 'login';
  const matched = session && tenant?.session === session ? tenant.status : undefined;
  const allowedRole = owner ? 'OWNER' : 'CUSTOMER';
  const booking = page === 'book' || page === 'book/availability';
  const detailId = /^(?:owner\/)?appointments\/(.+)$/.exec(page)?.[1];
  let validDetail = true;
  if (detailId) {
    try { resourceId(detailId); } catch { validDetail = false; }
  }

  return <main>
    <header className="site-header">
      <h1 ref={heading} tabIndex={-1}><bdi>{business?.status === 'ready' ? business.name : 'מספרה'}</bdi></h1>
      {session && <button type="button" disabled={busy} onClick={logout}>יציאה</button>}
    </header>
    {error && <p role="alert">{error}</p>}
    {!business ? <p role="status">טוענים את פרטי המספרה…</p>
      : business.status === 'missing' ? <p role="alert">המספרה לא נמצאה.</p>
      : business.status === 'error' ? <p role="alert">לא ניתן לטעון את פרטי המספרה.</p>
      : logoutPending ? <p role="status">האזור האישי מוסתר עד שהיציאה תושלם.</p>
      : session === undefined ? <p role="status">בודקים את מצב הכניסה…</p>
      : !session ? <form onSubmit={login}>
        <h2>כניסה לסביבת פיתוח</h2>
        <label htmlFor="access-code">קוד גישה</label>
        <input id="access-code" name="access_code" type="password" dir="ltr" autoComplete="off" required minLength={60} maxLength={60}/>
        <button disabled={busy || retryDelay > 0}>כניסה</button>
      </form>
      : matched === undefined ? <p role="status">בודקים את החיבור למספרה…</p>
      : matched === 'mismatch' ? <p role="alert">החיבור אינו מתאים למספרה זו. צאו כדי להיכנס מחדש.</p>
      : matched === 'error' ? <p role="alert">לא ניתן לבדוק את החיבור למספרה. אפשר לרענן את הדף.</p>
      : guarded && session.principal.role !== allowedRole ? <>
        <p role="alert">אין הרשאה לפתוח את העמוד הזה.</p>
        <a href={base}>חזרה לדף המספרה</a>
      </>
      : !validDetail ? <p role="alert">התור לא נמצא.</p>
      : <>
        <nav aria-label="ניווט ראשי">
          <a href={base}>דף המספרה</a>
          {session.principal.role === 'CUSTOMER' ? <>
            <a href={base + '/book'}>קביעת תור</a>
            <a href={base + '/appointments'}>התורים שלי</a>
          </> : <>
            <a href={base + '/owner/calendar'}>יומן</a>
            <a href={base + '/owner/book'}>תור חדש</a>
          </>}
        </nav>
        {booking ? <Customer slug={slug} availability={page === 'book/availability'} navigate={navigate}
          onSessionLost={loseSession}/>
          : guarded && <section>
            <h2>{page === 'owner/calendar' ? 'יומן תורים' : page === 'appointments' ? 'התורים שלי'
              : page === 'book/review' ? 'סקירת ההזמנה' : page === 'owner/book/customer' ? 'בחירת לקוח'
              : page === 'owner/book' ? 'תור חדש' : 'פרטי תור'}</h2>
            <p>המסך ייפתח לשימוש בשלב הבא של הפיתוח.</p>
          </section>}
      </>}
  </main>;
}

createRoot(document.getElementById('root')!).render(<App />);
