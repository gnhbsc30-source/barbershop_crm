import {decodeJson} from './transport';
import type {ResourceId} from './transport';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
  }
}

export type Session = {
  principal: {role: 'CUSTOMER' | 'OWNER'; subject: string; business_id: ResourceId};
  expires_at: string;
  csrf_token: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, {...init, credentials: 'same-origin', cache: 'no-store'});
  if (response.status === 204) return undefined as T;
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(response.status, body?.error?.code ?? 'HTTP_ERROR');
  }
  return decodeJson(await response.text()) as T;
}

export function customerApi<T>(slug: string, path: string, signal: AbortSignal): Promise<T> {
  return api<T>('/customer' + path, {signal, headers: {'X-Barbershop-Slug': slug}});
}
