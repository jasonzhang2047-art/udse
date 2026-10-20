/* Development sign-in: a bearer token for a synthetic fixture user, plus the chosen API server. */

import AsyncStorage from '@react-native-async-storage/async-storage';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { ApiError, conn, defaultBaseUrl, getCached } from './api';
import type { Me } from './types';

type Status = 'loading' | 'signedOut' | 'signedIn';

interface SessionValue {
  status: Status;
  me: Me | null;
  baseUrl: string;
  signIn: (token: string) => Promise<void>;
  signOut: () => Promise<void>;
  setBaseUrl: (url: string) => Promise<void>;
}

const SessionContext = createContext<SessionValue | null>(null);
const TOKEN_KEY = 'udse:token';
const URL_KEY = 'udse:api_url';

export function SessionProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading');
  const [me, setMe] = useState<Me | null>(null);
  const [baseUrl, setBase] = useState(conn.baseUrl);

  const loadMe = useCallback(async () => {
    try {
      const r = await getCached<Me>('/v1/me');
      setMe(r.data);
      setStatus('signedIn');
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        conn.token = null;
        await AsyncStorage.removeItem(TOKEN_KEY).catch(() => {});
      }
      setMe(null);
      setStatus('signedOut');
    }
  }, []);

  useEffect(() => {
    (async () => {
      const [token, url] = await Promise.all([AsyncStorage.getItem(TOKEN_KEY).catch(() => null), AsyncStorage.getItem(URL_KEY).catch(() => null)]);
      if (url !== null) {
        conn.baseUrl = url;
        setBase(url);
      }
      if (!token) {
        setStatus('signedOut');
        return;
      }
      conn.token = token;
      await loadMe();
    })();
  }, [loadMe]);

  const value = useMemo<SessionValue>(
    () => ({
      status,
      me,
      baseUrl,
      signIn: async (token) => {
        conn.token = token;
        await AsyncStorage.setItem(TOKEN_KEY, token).catch(() => {});
        await loadMe();
      },
      signOut: async () => {
        conn.token = null;
        await AsyncStorage.removeItem(TOKEN_KEY).catch(() => {});
        setMe(null);
        setStatus('signedOut');
      },
      setBaseUrl: async (url) => {
        const clean = url.trim().replace(/\/+$/, '');
        conn.baseUrl = clean || defaultBaseUrl();
        setBase(conn.baseUrl);
        if (clean) await AsyncStorage.setItem(URL_KEY, clean).catch(() => {});
        else await AsyncStorage.removeItem(URL_KEY).catch(() => {});
      },
    }),
    [status, me, baseUrl, loadMe],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(SessionContext);
  if (!v) throw new Error('useSession outside SessionProvider');
  return v;
}
