import {isLosslessNumber, LosslessNumber, parse, stringify} from 'lossless-json';

declare const idBrand: unique symbol;
export type ResourceId = string & {[idBrand]: true};

const maxId = 9223372036854775807n;
const idKeys = new Set(['business_id', 'staff_id', 'service_id', 'customer_id', 'owner_user_id',
  'appointment_id', 'next_before_id', 'next_after_id', 'before_id', 'after_id']);

export function resourceId(value: string): ResourceId {
  if (!/^[1-9][0-9]{0,18}$/.test(value) || BigInt(value) > maxId) {
    throw new Error('Invalid resource identifier');
  }
  return value as ResourceId;
}

function decodeId(value: unknown): ResourceId {
  if (!isLosslessNumber(value)) throw new Error('Expected a JSON integer identifier');
  return resourceId(value.value);
}

export function decodeJson(text: string): unknown {
  const decoded = parse(text);
  function visit(value: unknown, key = ''): unknown {
    if (key === 'service_ids') {
      if (!Array.isArray(value)) throw new Error('Expected identifiers');
      return value.map(decodeId);
    }
    if (idKeys.has(key) && value !== null) return decodeId(value);
    if (isLosslessNumber(value)) return value.valueOf();
    if (Array.isArray(value)) return value.map(item => visit(item));
    if (value !== null && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, visit(item, name)]));
    }
    return value;
  }
  return visit(decoded);
}

export function encodeJson(value: unknown): string {
  const encodeId = (id: unknown) => {
    if (typeof id !== 'string') throw new Error('Expected a decimal identifier string');
    return new LosslessNumber(resourceId(id));
  };
  const result = stringify(value, (key, current) => {
    if (key === 'service_ids') {
      if (!Array.isArray(current)) throw new Error('Expected identifiers');
      return current.map(encodeId);
    }
    if (idKeys.has(key) && current !== null) return encodeId(current);
    return current;
  });
  if (result === undefined) throw new Error('Expected a JSON value');
  return result;
}

export function slotQuery(date: string, staff: ResourceId, services: ResourceId[]): string {
  if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(date)) throw new Error('Invalid business date');
  const query = new URLSearchParams({date, staff_id: resourceId(staff)});
  for (const service of services) query.append('service_id', resourceId(service));
  return query.toString();
}
