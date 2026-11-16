import {useEffect, useState} from 'react';
import {ApiError, customerApi} from './api';
import {resourceId, slotQuery} from './transport';
import type {ResourceId} from './transport';

type Service = {service_id: ResourceId; name: string};
type Staff = {staff_id: ResourceId; staff_name: string};
type Slot = {start_datetime: string; end_datetime: string};

type Load<T> = {status: 'loading'} | {status: 'error'; message: string} | {status: 'ready'; data: T};

function useCustomerRead<T>(slug: string, path: string, onSessionLost: () => void): Load<T> {
  const [result, setResult] = useState<Load<T>>({status: 'loading'});
  useEffect(() => {
    const controller = new AbortController();
    customerApi<T>(slug, path, controller.signal).then(
      data => { if (!controller.signal.aborted) setResult({status: 'ready', data}); },
      failure => {
        if (controller.signal.aborted) return;
        if (failure instanceof ApiError && failure.status === 401) {
          onSessionLost();
          return;
        }
        const code = failure instanceof ApiError ? failure.message : '';
        const message = code === 'BOOKING_WINDOW_EXCEEDED' || code === 'PAST_DATETIME'
          ? 'התאריך מחוץ לטווח ההזמנות.'
          : code === 'AVAILABLE_SLOTS_BUSY' ? 'חיפוש השעות עמוס כרגע. אפשר לרענן בעוד רגע.'
          : 'לא ניתן לטעון את המידע.';
        setResult({status: 'error', message});
      },
    );
    return () => controller.abort();
  }, [slug, path, onSessionLost]);
  return result;
}

function Status({load}: {load: Load<unknown>}) {
  if (load.status === 'loading') return <p role="status">טוענים…</p>;
  if (load.status === 'error') return <p role="alert">{load.message}</p>;
  return null;
}

function Times({slug, query, onSessionLost}: {slug: string; query: string; onSessionLost: () => void}) {
  const result = useCustomerRead<{slots: Slot[]}>(slug, '/available-slots?' + query, onSessionLost);
  if (result.status !== 'ready') return <Status load={result}/>;
  if (!result.data.slots.length) return <p role="status">אין שעות פנויות ביום הזה. אפשר לבחור יום או איש צוות אחרים.</p>;
  return <fieldset>
    <legend>שעות זמינות</legend>
    <p>בחירת שעה אינה שומרת תור.</p>
    <div className="slots">
      {result.data.slots.map(slot => <label className="slot" key={slot.start_datetime}>
        <input type="radio" name="slot" value={slot.start_datetime}/>
        <bdi dir="ltr">{clockLabel(slot.start_datetime)}</bdi>
      </label>)}
    </div>
  </fieldset>;
}

function clockLabel(localTime: string) {
  const clock = localTime.slice(11);
  return clock.endsWith(':00') && clock.length === 8 ? clock.slice(0, 5) : clock;
}

function businessToday() {
  const formatter = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Jerusalem', year: 'numeric', month: '2-digit', day: '2-digit',
  });
  const parts = Object.fromEntries(formatter.formatToParts().map(part => [part.type, part.value]));
  return [parts.year, parts.month, parts.day].join('-');
}

function Availability({slug, services, onSessionLost}: {
  slug: string; services: ResourceId[]; onSessionLost: () => void;
}) {
  const query = new URLSearchParams(services.map(id => ['service_id', id]));
  const staff = useCustomerRead<{staff: Staff[]}>(slug, '/staff?' + query, onSessionLost);
  const [selected, select] = useState<ResourceId | ''>('');
  const [date, setDate] = useState(businessToday);
  const [refresh, setRefresh] = useState(0);
  const slots = selected && /^\d{4}-\d{2}-\d{2}$/.test(date) ? slotQuery(date, selected, services) : '';
  if (staff.status !== 'ready') return <Status load={staff}/>;
  if (!staff.data.staff.length) return <p role="status">אין צוות שמתאים לשילוב השירותים שנבחר.</p>;
  return <section aria-labelledby="availability-heading">
    <h2 id="availability-heading">צוות ויום</h2>
    <div className="filters">
      <label>איש צוות
        <select value={selected} onChange={event => select(event.target.value ? resourceId(event.target.value) : '')}>
          <option value="">בחירת איש צוות</option>
          {staff.data.staff.map(item => <option key={item.staff_id} value={item.staff_id}>{item.staff_name}</option>)}
        </select>
      </label>
      <label>תאריך
        <input dir="ltr" type="date" value={date} min={businessToday()} onChange={event => setDate(event.target.value)}/>
      </label>
    </div>
    {slots && <>
      <button type="button" onClick={() => setRefresh(value => value + 1)}>רענון שעות</button>
      <Times key={slots + '&refresh=' + refresh} slug={slug} query={slots} onSessionLost={onSessionLost}/>
    </>}
  </section>;
}

export function Customer({slug, availability, navigate, onSessionLost}: {
  slug: string; availability: boolean; navigate: (page: string) => void; onSessionLost: () => void;
}) {
  const catalog = useCustomerRead<{services: Service[]}>(slug, '/services', onSessionLost);
  const [services, setServices] = useState<ResourceId[]>([]);
  if (catalog.status !== 'ready') return <Status load={catalog}/>;
  if (!catalog.data.services.length) return <p role="status">אין שירותים זמינים להזמנה.</p>;
  return <>
    <h2>בחירת שירותים</h2>
    <fieldset>
      <legend>שירותים להזמנה</legend>
      {catalog.data.services.map(service => <label className="option" key={service.service_id}>
        <input type="checkbox" checked={services.includes(service.service_id)}
          disabled={services.length >= 20 && !services.includes(service.service_id)}
          onChange={event => {
            const checked = event.target.checked;
            setServices(current => checked ? [...current, service.service_id] : current.filter(id => id !== service.service_id));
          }}/>
        {service.name}
      </label>)}
    </fieldset>
    {!availability && <button type="button" disabled={!services.length} onClick={() => navigate('book/availability')}>
      לבחירת צוות ויום
    </button>}
    {availability && (services.length
      ? <Availability key={services.join(',')} slug={slug} services={services} onSessionLost={onSessionLost}/>
      : <p>בחרו שירות כדי לראות שעות פנויות.</p>)}
  </>;
}
