/**
 * The search in the header belongs to the page that is open: a page that lists things by name registers it, with its own words, and it goes when the person
 * leaves. A page without one shows none, so the header never offers a search that looks for something the page is not about.
 */

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export interface HeaderSearchConfig {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  testid: string;
}

const Ctx = createContext<{ config: HeaderSearchConfig | null; set: (c: HeaderSearchConfig | null) => void }>({ config: null, set: () => {} });

export function HeaderSearchProvider({ children }: { children: ReactNode }) {
  const [config, set] = useState<HeaderSearchConfig | null>(null);
  return <Ctx.Provider value={{ config, set }}>{children}</Ctx.Provider>;
}

/** What the header shows as its search (the shell reads this). */
export const useHeaderSearchConfig = () => useContext(Ctx).config;

/** Registers the page's search for as long as the page is open. */
export function useHeaderSearch({ value, onChange, placeholder, testid }: HeaderSearchConfig) {
  const { set } = useContext(Ctx);
  const latest = useRef(onChange);
  latest.current = onChange;
  useEffect(() => {
    set({ value, onChange: (v) => latest.current(v), placeholder, testid });
  }, [set, value, placeholder, testid]);
  useEffect(() => () => set(null), [set]);
}
