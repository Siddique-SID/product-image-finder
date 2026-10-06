export function messageFor(data: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (typeof data === 'string') return data;
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as {detail: unknown}).detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail)) return detail.map(item => typeof item?.msg === 'string' ? item.msg : '').filter(Boolean).join(' · ') || fallback;
  }
  return fallback;
}
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  try {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(messageFor(data, response.status === 401 ? 'Your session expired. Please sign in again.' : 'The server could not complete this request. Please try again.'), response.status);
    return data as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new Error('Connection interrupted. Check your internet connection and try again.');
  }
}
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename;
  document.body.appendChild(anchor); anchor.click(); anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
