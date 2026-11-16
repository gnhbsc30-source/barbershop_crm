export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
  }
}

export type Session = {
  principal: {role: 'CUSTOMER' | 'OWNER'};
  expires_at: string;
  csrf_token: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, {...init, credentials: 'same-origin', cache: 'no-store'});
  if (response.status === 204) return undefined as T;
  const body = await response.json();
  if (!response.ok) throw new ApiError(response.status, body.error?.code ?? 'HTTP_ERROR');
  return body as T;
}
