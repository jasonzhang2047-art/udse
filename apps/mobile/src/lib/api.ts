/* HTTP client for the UDSE API: bearer tokens, async jobs, and an offline cache of viewed results.
 *
 * Every successful GET is saved on the device. When the network is unreachable the last saved copy is
 * returned instead and flagged, so screens can say "saved on this device at …". Simulated values stay
 * labelled as simulated either way; the cache never feeds anything back to the server. */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

import type { Job, JobRef } from './types';

const ENV_URL = process.env.EXPO_PUBLIC_UDSE_API_URL;

/** Where the API lives unless the user picked another server on the sign-in screen. */
export function defaultBaseUrl(): string {
  if (ENV_URL) return ENV_URL.replace(/\/+$/, '');
  // Web: same origin. In development the Expo dev server proxies /v1 to the backend (metro.config.js).
  if (Platform.OS === 'web') return '';
  // The Android emulator reaches the host machine through 10.0.2.2; the iOS simulator shares localhost.
  if (Platform.OS === 'android') return 'http://10.0.2.2:8765';
  return 'http://127.0.0.1:8765';
}

export const conn = { baseUrl: defaultBaseUrl(), token: null as string | null };

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** Thrown when the server could not be reached at all (as opposed to an HTTP error). */
export class OfflineError extends Error {}

export interface Fetched<T> {
  data: T;
  /** ISO time the copy was saved, when served from the device cache because the network failed. */
  cachedAt: string | null;
}

type Body = Record<string, unknown> | unknown[];

async function request<T>(path: string, method = 'GET', body?: Body): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${conn.baseUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', ...(conn.token ? { Authorization: `Bearer ${conn.token}` } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new OfflineError(`Can't reach the UDSE server at ${conn.baseUrl || 'this origin'} (${(e as Error).message}).`);
  }
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { detail: text };
  }
  if (!res.ok) {
    const d = data as { detail?: unknown; error?: unknown } | null;
    const detail = d?.detail ?? d?.error;
    const msg = typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : res.statusText || `HTTP ${res.status}`;
    // A gateway (e.g. the dev proxy) answering for an unreachable backend counts as offline.
    if (res.status === 502 || res.status === 503 || res.status === 504) throw new OfflineError(msg);
    throw new ApiError(msg, res.status, data);
  }
  return data as T;
}

/* ------------------------------------------------------------------ offline cache */
const INDEX_KEY = 'udse:cache:index';
const MAX_ENTRIES = 40;
const cacheKey = (path: string) => `udse:cache:${conn.baseUrl}|${conn.token ?? ''}|${path}`;
// Job status is live state, and a promotion check is an action (each call re-attempts every path).
const cacheable = (path: string) => !path.startsWith('/v1/jobs/') && !path.endsWith('/promotion-check');

async function remember(path: string, data: unknown): Promise<void> {
  if (!cacheable(path)) return;
  const key = cacheKey(path);
  try {
    const index: string[] = JSON.parse((await AsyncStorage.getItem(INDEX_KEY)) || '[]');
    const next = [key, ...index.filter((k) => k !== key)];
    const evicted = next.splice(MAX_ENTRIES);
    if (evicted.length) await AsyncStorage.multiRemove(evicted);
    await AsyncStorage.setItem(key, JSON.stringify({ at: new Date().toISOString(), data }));
    await AsyncStorage.setItem(INDEX_KEY, JSON.stringify(next));
  } catch {
    // Storage full or unavailable: the cache is a convenience, never required.
  }
}

async function recall<T>(path: string): Promise<{ at: string; data: T } | null> {
  try {
    const raw = await AsyncStorage.getItem(cacheKey(path));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function clearCache(): Promise<void> {
  try {
    const index: string[] = JSON.parse((await AsyncStorage.getItem(INDEX_KEY)) || '[]');
    await AsyncStorage.multiRemove([...index, INDEX_KEY]);
  } catch {
    // ignore
  }
}

/** GET with the offline fallback. */
export async function getCached<T>(path: string): Promise<Fetched<T>> {
  try {
    const data = await request<T>(path);
    void remember(path, data);
    return { data, cachedAt: null };
  } catch (e) {
    if (e instanceof OfflineError) {
      const hit = await recall<T>(path);
      if (hit) return { data: hit.data, cachedAt: hit.at };
    }
    throw e;
  }
}

export const api = {
  get: <T>(path: string) => getCached<T>(path).then((r) => r.data),
  post: <T>(path: string, body: Body = {}) => request<T>(path, 'POST', body),
};

/** Poll an async job until it finishes; returns its result. */
export async function waitJob<T = unknown>(ref: JobRef | string, onTick?: (j: Job) => void, signal?: { cancelled: boolean }): Promise<T> {
  const id = typeof ref === 'string' ? ref : ref.job_id;
  for (let i = 0; i < 900; i++) {
    if (signal?.cancelled) throw new Error('cancelled');
    const j = await request<Job>(`/v1/jobs/${id}`);
    onTick?.(j);
    if (j.status === 'SUCCEEDED') return j.result as T;
    if (j.status === 'FAILED') throw new Error((j.error || 'job failed').split('\n')[0]);
    await new Promise((r) => setTimeout(r, 700));
  }
  throw new Error('job timed out');
}
